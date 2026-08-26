"""The investigation loop.

Detect -> gather -> reason -> cite -> resolve or escalate.

The loop itself is ordinary code; the model only chooses which tool to call
next and what the evidence means. Everything that could go wrong for a finance
team is handled deterministically around it:

* **Invented records are discarded.** Every id the model cites is checked
  against the database before it counts as evidence. A hallucinated refund
  cannot explain a rupee.
* **The residual is computed, not claimed.** ``unexplained = delta - sum(cited
  evidence)``. A case resolves only when that reaches zero within tolerance,
  regardless of how confident the model says it is.
* **Every failure escalates.** An API outage, a malformed call, or a loop that
  runs out of budget produces UNRESOLVED and a human review - never a guess.
* **The audit trail is written in one transaction at the end.** The agent role
  holds INSERT and no UPDATE on `investigation_steps`, so a trace cannot be
  edited after the fact; writing it whole keeps that guarantee intact.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from sqlalchemy import Connection, text

from backend.agents.evidence import (
    Citation,
    EvidencePackage,
    build_package,
    verify_citations,
)
from backend.agents.llm import GeminiProvider, LLMError, LLMResponse
from backend.agents.prompts import PROMPT_VERSION, SYSTEM_PROMPT, opening_message
from backend.agents.tools import (
    SUBMIT_FINDING,
    TOOL_SCHEMAS,
    ToolContext,
    ToolError,
    run_tool,
)
from backend.config import FinancialConfig
from backend.enums import ExceptionStatus, ExceptionType, InvestigationMode
from backend.models import Evidence, Investigation, InvestigationStep
from backend.money import format_paise

@dataclass
class Step:
    seq: int
    step_type: str
    tool_name: str | None = None
    tool_args: dict | None = None
    tool_result: dict | None = None
    observation: str | None = None
    duration_ms: int = 0


@dataclass
class Result:
    investigation_id: str
    exception_id: str
    payment_id: str
    delta: int
    started_at: datetime
    completed_at: datetime | None = None
    model: str = ""
    cause_type: str | None = None
    summary: str = ""
    reasoning_confidence: int | None = None
    unexplained_amount: int | None = None
    final_status: str = ExceptionStatus.UNRESOLVED.value
    evidence: list[Citation] = field(default_factory=list)
    rejected_evidence: list[Citation] = field(default_factory=list)
    package: EvidencePackage | None = None
    steps: list[Step] = field(default_factory=list)
    tool_call_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    llm_requests: int = 0
    cache_hits: int = 0
    latency_ms: int = 0
    error: str | None = None

    @property
    def resolved(self) -> bool:
        return self.final_status == ExceptionStatus.RESOLVED.value


def _emit(result: "Result", on_step, step: Step) -> None:
    result.steps.append(step)
    if on_step is not None:
        on_step(step)


def _investigation_id(exception_id: str, model: str) -> str:
    digest = hashlib.sha1(f"{exception_id}:{model}:{PROMPT_VERSION}".encode()).hexdigest()
    return f"inv_{digest[:16]}"


def investigate(
    conn: Connection,
    provider: GeminiProvider,
    *,
    exception_id: str,
    payment_id: str,
    delta: int,
    cfg: FinancialConfig,
    as_of: date,
    max_tool_calls: int = 8,
    rule_flag: str | None = None,
    on_step=None,
) -> Result:
    """Run one investigation. Never raises - failures become escalations.

    ``on_step`` is called with each Step as it happens, so a long-running batch
    reports progress instead of going silent.
    """
    started = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    result = Result(
        investigation_id=_investigation_id(exception_id, provider.model),
        exception_id=exception_id,
        payment_id=payment_id,
        delta=delta,
        started_at=started,
        model=provider.model,
    )
    ctx = ToolContext(conn=conn, cfg=cfg, as_of=as_of)
    history = [
        provider.user_turn(
            opening_message(
                exception_id, payment_id, delta, format_paise(delta), rule_flag
            )
        )
    ]
    seq = 0

    try:
        while result.tool_call_count < max_tool_calls:
            step_t = time.perf_counter()
            response: LLMResponse = provider.send(
                system=SYSTEM_PROMPT, history=history, tools=TOOL_SCHEMAS
            )
            seq += 1
            result.input_tokens += response.input_tokens
            result.output_tokens += response.output_tokens
            result.llm_requests += 0 if response.from_cache else 1
            result.cache_hits += int(response.from_cache)
            _emit(result, on_step, Step(
                seq=seq,
                step_type="llm_call",
                observation=response.text.strip() or None,
                duration_ms=int((time.perf_counter() - step_t) * 1000),
            ))

            if not response.tool_calls:
                # No tool call and no finding: the model has stopped without
                # answering. Treat silence as an escalation, never as consent.
                result.error = "model ended the turn without calling submit_finding"
                break

            history.append(response.raw_model_turn)

            finding = next(
                (c for c in response.tool_calls if c.name == SUBMIT_FINDING), None
            )
            if finding is not None:
                seq += 1
                _emit(result, on_step, Step(
                    seq=seq, step_type="finding", tool_name=SUBMIT_FINDING,
                    tool_args=finding.args,
                ))
                _apply_finding(conn, result, finding.args, cfg)
                break

            outputs: list[tuple[str, dict]] = []
            for call in response.tool_calls:
                result.tool_call_count += 1
                call_t = time.perf_counter()
                try:
                    payload = run_tool(ctx, call.name, dict(call.args))
                    observation = None
                except ToolError as exc:
                    payload = {"error": str(exc)}
                    observation = f"tool refused: {exc}"
                seq += 1
                _emit(result, on_step, Step(
                    seq=seq,
                    step_type="tool_call",
                    tool_name=call.name,
                    tool_args=dict(call.args),
                    tool_result=payload,
                    observation=observation,
                    duration_ms=int((time.perf_counter() - call_t) * 1000),
                ))
                outputs.append((call.name, payload))
            history.append(provider.tool_result_turn(outputs))
        else:
            result.error = (
                f"tool-call budget of {max_tool_calls} exhausted without a finding"
            )

    except LLMError as exc:
        result.error = f"LLM unavailable: {exc}"
    except Exception as exc:  # defensive: an investigator must not crash a batch
        result.error = f"{type(exc).__name__}: {exc}"

    if result.error:
        seq += 1
        _emit(result, on_step, Step(seq=seq, step_type="error", observation=result.error))
        result.final_status = ExceptionStatus.UNRESOLVED.value
        result.unexplained_amount = delta
        result.cause_type = result.cause_type or ExceptionType.UNKNOWN_DISCREPANCY.value
        result.summary = result.summary or f"Escalated without a conclusion: {result.error}"

    result.completed_at = datetime.now(timezone.utc)
    result.latency_ms = int((time.perf_counter() - t0) * 1000)
    return result


def _apply_finding(
    conn: Connection, result: Result, args: dict, cfg: FinancialConfig
) -> None:
    """Verify the citations, compute the residual, and decide the outcome."""
    result.cause_type = args.get("cause_type") or ExceptionType.UNKNOWN_DISCREPANCY.value
    result.summary = (args.get("summary") or "").strip()
    confidence = args.get("confidence")
    result.reasoning_confidence = confidence if isinstance(confidence, int) else None
    declared_unresolved = bool(args.get("unresolved"))

    raw_evidence = args.get("evidence") or []
    if not isinstance(raw_evidence, list):
        raw_evidence = []
    citations = verify_citations(
        conn,
        [e for e in raw_evidence if isinstance(e, dict)],
        payment_id=result.payment_id,
        delta=result.delta,
        cause_type=result.cause_type,
        tolerance=cfg.tolerance_paise,
    )
    package = build_package(
        citations,
        payment_id=result.payment_id,
        delta=result.delta,
        cause_type=result.cause_type,
    )
    result.package = package
    result.evidence = package.verified
    result.rejected_evidence = package.rejected

    if declared_unresolved:
        # The model said it cannot explain this. Its citations do not then get
        # to zero the residual - an escalation that reports "nothing
        # unexplained" tells a human the opposite of what it means.
        result.unexplained_amount = result.delta
        result.final_status = ExceptionStatus.ESCALATED.value
        return

    result.unexplained_amount = package.unexplained

    if not package.verified:
        # Nothing survived verification, so nothing has been explained.
        result.unexplained_amount = result.delta
        result.final_status = ExceptionStatus.ESCALATED.value
    elif result.cause_type == ExceptionType.UNKNOWN_DISCREPANCY.value:
        # "Resolved as unknown" is a contradiction. If the cause is unknown the
        # case belongs with a human, whatever the arithmetic came to.
        result.final_status = ExceptionStatus.ESCALATED.value
    elif abs(package.unexplained) <= cfg.tolerance_paise:
        result.final_status = ExceptionStatus.RESOLVED.value
    else:
        # Partly explained. A human sees it with the residual stated - the
        # system never rounds a leftover away to claim a resolution.
        result.final_status = ExceptionStatus.REVIEW.value



# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------


def persist(conn: Connection, result: Result) -> None:
    """Write the investigation, its steps and its evidence in one transaction."""
    conn.execute(
        Investigation.__table__.insert(),
        {
            "investigation_id": result.investigation_id,
            "exception_id": result.exception_id,
            "mode": InvestigationMode.AI.value,
            "started_at": result.started_at,
            "completed_at": result.completed_at,
            "llm_model": result.model,
            "prompt_version": PROMPT_VERSION,
            "reasoning_confidence": result.reasoning_confidence,
            "evidence_score": None,  # Phase 9 computes this
            "decision": result.summary,
            "final_status": result.final_status,
            "unexplained_amount": result.unexplained_amount,
            "tool_call_count": result.tool_call_count,
            "tokens_used": result.input_tokens + result.output_tokens,
            "latency_ms": result.latency_ms,
        },
    )
    if result.steps:
        conn.execute(
            InvestigationStep.__table__.insert(),
            [
                {
                    "step_id": f"{result.investigation_id}_{s.seq:03d}",
                    "investigation_id": result.investigation_id,
                    "seq": s.seq,
                    "step_type": s.step_type,
                    "tool_name": s.tool_name,
                    "tool_args": s.tool_args,
                    "tool_result": s.tool_result,
                    "observation": s.observation,
                    "duration_ms": s.duration_ms,
                    "created_at": result.started_at,
                }
                for s in result.steps
            ],
        )
    rows = [
        {
            "evidence_id": f"{result.investigation_id}_ev{i:02d}",
            "investigation_id": result.investigation_id,
            "record_type": c.record_type,
            "record_id": c.record_id,
            "role": "SUPPORTS",
            "amount_contribution": c.claimed,
            "note": c.note,
        }
        for i, c in enumerate(result.evidence)
    ] + [
        # Rejections are recorded, not silently dropped: a reviewer must be
        # able to see what the model claimed and exactly why it did not stand.
        {
            "evidence_id": f"{result.investigation_id}_rj{i:02d}",
            "investigation_id": result.investigation_id,
            "record_type": c.record_type[:32],
            "record_id": c.record_id[:64],
            "role": "CONTRADICTS",
            "amount_contribution": None,
            "note": f"REJECTED ({c.reason}) - claimed {c.claimed} paise. {c.note}"[:2000],
        }
        for i, c in enumerate(result.rejected_evidence)
    ]
    if rows:
        conn.execute(Evidence.__table__.insert(), rows)
