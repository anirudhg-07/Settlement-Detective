"""G11 - the AI investigation agent.

Almost every test here runs against a scripted fake model, so the safety rules
are exercised exhaustively without spending a single API request. That matters:
the project runs on a free key with 500 requests a day, and a test suite that
called a live model would be unrunnable.

The tests that matter most are the ones about *refusing* to resolve. A
confident wrong explanation sends a finance analyst hunting in the wrong place,
and false resolutions are the one metric this product cannot afford.
"""

from __future__ import annotations

from datetime import date

import pytest
import sqlalchemy as sa

from backend.agents.investigator import investigate, persist
from backend.agents.llm import LLMError, LLMResponse, RateLimiter, ResponseCache, ToolCall
from backend.agents.tools import (
    SUBMIT_FINDING,
    TOOL_SCHEMAS,
    ToolContext,
    ToolError,
    run_tool,
)
from backend.config import FinancialConfig, get_settings
from backend.enums import ExceptionStatus, ExceptionType

AS_OF = date(2026, 1, 31)


# --------------------------------------------------------------------------
# A scripted model
# --------------------------------------------------------------------------


class FakeProvider:
    """Replays a fixed list of responses. No network, no quota."""

    model = "fake-model"

    def __init__(self, script: list[LLMResponse | Exception]) -> None:
        self.script = list(script)
        self.sent: list[dict] = []

    def send(self, *, system: str, history: list[dict], tools: list[dict]) -> LLMResponse:
        self.sent.append({"system": system, "history": list(history), "tools": tools})
        if not self.script:
            raise AssertionError("the agent asked for more turns than were scripted")
        nxt = self.script.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    @staticmethod
    def user_turn(text: str) -> dict:
        return {"role": "user", "parts": [{"text": text}]}

    @staticmethod
    def tool_result_turn(results):
        return {"role": "user", "parts": [{"functionResponse": {"name": n, "response": p}}
                                          for n, p in results]}


def finding(**kwargs) -> LLMResponse:
    args = {
        "cause_type": ExceptionType.FEE_MISMATCH.value,
        "summary": "the gateway over-deducted the fee",
        "evidence": [],
        "unresolved": False,
        "confidence": 90,
    }
    args.update(kwargs)
    return LLMResponse(
        tool_calls=(ToolCall(name=SUBMIT_FINDING, args=args),),
        raw_model_turn={"role": "model", "parts": []},
    )


@pytest.fixture(scope="module")
def cfg_mod() -> FinancialConfig:
    return get_settings().financial()


@pytest.fixture
def conn():
    from backend.db.session import agent_engine

    try:
        engine = agent_engine()
        with engine.connect() as probe:
            probe.execute(sa.text("SELECT 1"))
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"PostgreSQL/sd_agent unavailable ({exc})")
    with engine.connect() as connection:
        tx = connection.begin()
        try:
            if connection.execute(sa.text("SELECT count(*) FROM ops.payments")).scalar() == 0:
                pytest.skip("no dataset loaded")
            yield connection
        finally:
            tx.rollback()


@pytest.fixture
def a_case(conn) -> dict:
    row = conn.execute(
        sa.text(
            "SELECT e.exception_id, e.payment_id, e.delta FROM recon.exceptions e"
            " WHERE e.exception_type = 'FEE_MISMATCH' AND e.delta <> 0 LIMIT 1"
        )
    ).mappings().one()
    fee_id = conn.execute(
        sa.text("SELECT fee_id FROM ops.fees WHERE payment_id = :p"),
        {"p": row["payment_id"]},
    ).scalar()
    return {**row, "fee_id": fee_id}


def run(conn, cfg, case, script, **kw):
    return investigate(
        conn, FakeProvider(script),
        exception_id=case["exception_id"], payment_id=case["payment_id"],
        delta=case["delta"], cfg=cfg, as_of=AS_OF, **kw,
    )


# --------------------------------------------------------------------------
# The safety rules
# --------------------------------------------------------------------------


@pytest.mark.db
def test_a_payment_cannot_be_evidence_for_its_own_discrepancy(conn, cfg_mod, a_case):
    """The false-resolution generator this guard exists to stop.

    Citing the payment under investigation for the whole delta drives the
    residual to zero and manufactures a confident RESOLVED out of nothing.
    """
    result = run(conn, cfg_mod, a_case, [finding(evidence=[
        {"record_type": "payment", "record_id": a_case["payment_id"],
         "amount_paise": a_case["delta"], "note": "the payment"},
    ])])
    assert result.evidence == []
    assert result.rejected_evidence
    assert "cannot be the evidence for its own" in result.rejected_evidence[0].reason
    assert result.unexplained_amount == a_case["delta"]
    assert result.final_status == ExceptionStatus.ESCALATED.value


@pytest.mark.db
def test_missing_settlement_may_cite_the_payment_when_code_confirms_the_absence(
    conn, cfg_mod
):
    """The one legitimate self-citation - and the database verifies it."""
    row = conn.execute(
        sa.text(
            "SELECT exception_id, payment_id, delta FROM recon.exceptions"
            " WHERE exception_type = 'MISSING_SETTLEMENT' LIMIT 1"
        )
    ).mappings().one()
    result = run(conn, cfg_mod, dict(row), [finding(
        cause_type=ExceptionType.MISSING_SETTLEMENT.value,
        evidence=[{"record_type": "payment", "record_id": row["payment_id"],
                   "amount_paise": row["delta"], "note": "never settled"}],
    )])
    assert result.evidence and result.evidence[0].snapshot == {"settled_credit_lines": 0}
    assert result.final_status == ExceptionStatus.RESOLVED.value


@pytest.mark.db
def test_a_payment_that_did_settle_cannot_claim_a_missing_settlement(conn, cfg_mod, a_case):
    """Saying MISSING_SETTLEMENT does not make it so - the code checks."""
    result = run(conn, cfg_mod, a_case, [finding(
        cause_type=ExceptionType.MISSING_SETTLEMENT.value,
        evidence=[{"record_type": "payment", "record_id": a_case["payment_id"],
                   "amount_paise": a_case["delta"], "note": "claims never settled"}],
    )])
    assert result.evidence == []
    assert result.final_status == ExceptionStatus.ESCALATED.value


@pytest.mark.db
def test_invented_records_are_discarded(conn, cfg_mod, a_case):
    """A hallucinated refund cannot explain a rupee."""
    result = run(conn, cfg_mod, a_case, [finding(evidence=[
        {"record_type": "refund", "record_id": "rfnd_TOTALLYMADEUP",
         "amount_paise": a_case["delta"], "note": "invented"},
    ])])
    assert result.evidence == []
    assert "no such record" in result.rejected_evidence[0].reason
    assert result.unexplained_amount == a_case["delta"]
    assert result.final_status == ExceptionStatus.ESCALATED.value


@pytest.mark.db
def test_declaring_unresolved_keeps_the_whole_discrepancy_unexplained(conn, cfg_mod, a_case):
    """An escalation reporting 'nothing unexplained' tells a human the opposite
    of what it means."""
    result = run(conn, cfg_mod, a_case, [finding(
        unresolved=True, confidence=0,
        evidence=[{"record_type": "fee", "record_id": a_case["fee_id"],
                   "amount_paise": a_case["delta"], "note": "attached anyway"}],
    )])
    # Even a citation that WOULD verify does not close an escalation.
    assert result.unexplained_amount == a_case["delta"]
    assert result.final_status == ExceptionStatus.ESCALATED.value


@pytest.mark.db
def test_resolved_as_unknown_is_a_contradiction(conn, cfg_mod, a_case):
    """If the cause is unknown the case belongs with a human, whatever the
    arithmetic came to."""
    result = run(conn, cfg_mod, a_case, [finding(
        cause_type=ExceptionType.UNKNOWN_DISCREPANCY.value,
        evidence=[{"record_type": "fee", "record_id": a_case["fee_id"],
                   "amount_paise": a_case["delta"], "note": "accounts for it"}],
    )])
    assert result.final_status == ExceptionStatus.ESCALATED.value


@pytest.mark.db
def test_a_partial_explanation_goes_to_review_with_the_residual_stated(
    conn, cfg_mod, a_case
):
    """The system never rounds a leftover away to claim a resolution.

    The cited amount has to be one the record can genuinely support, or it is
    rejected outright (see G12) - so this uses a real supported figure that
    happens to cover only part of the discrepancy.
    """
    from backend.agents.evidence import supported_contributions

    supported, _ = supported_contributions(
        conn, "fee", a_case["fee_id"], a_case["payment_id"]
    )
    partial = next(
        (s for s in supported if abs(s - a_case["delta"]) > cfg_mod.tolerance_paise), None
    )
    if partial is None:
        pytest.skip("this fee record supports only the full delta")

    result = run(conn, cfg_mod, a_case, [finding(evidence=[
        {"record_type": "fee", "record_id": a_case["fee_id"],
         "amount_paise": partial, "note": "part of it"},
    ])])
    assert result.final_status == ExceptionStatus.REVIEW.value
    assert result.unexplained_amount == a_case["delta"] - partial


@pytest.mark.db
def test_a_fully_evidenced_case_resolves(conn, cfg_mod, a_case):
    result = run(conn, cfg_mod, a_case, [finding(evidence=[
        {"record_type": "fee", "record_id": a_case["fee_id"],
         "amount_paise": a_case["delta"], "note": "over-deducted fee"},
    ])])
    assert result.final_status == ExceptionStatus.RESOLVED.value
    assert result.unexplained_amount == 0


# --------------------------------------------------------------------------
# Failure modes must escalate, never guess
# --------------------------------------------------------------------------


@pytest.mark.db
def test_an_llm_outage_escalates(conn, cfg_mod, a_case):
    result = run(conn, cfg_mod, a_case, [LLMError("503 backend unavailable")])
    assert result.final_status == ExceptionStatus.UNRESOLVED.value
    assert result.unexplained_amount == a_case["delta"]
    assert "LLM unavailable" in result.error
    assert result.steps[-1].step_type == "error"


@pytest.mark.db
def test_running_out_of_tool_budget_escalates(conn, cfg_mod, a_case):
    """An agent that cannot decide within its budget hands over, not guesses."""
    spin = LLMResponse(
        tool_calls=(ToolCall(name="get_case_bundle",
                             args={"payment_id": a_case["payment_id"]}),),
        raw_model_turn={"role": "model", "parts": []},
    )
    result = run(conn, cfg_mod, a_case, [spin] * 10, max_tool_calls=3)
    assert result.tool_call_count == 3
    assert result.final_status == ExceptionStatus.UNRESOLVED.value
    assert "budget" in result.error


@pytest.mark.db
def test_a_model_that_stops_without_answering_escalates(conn, cfg_mod, a_case):
    """Silence is not consent."""
    result = run(conn, cfg_mod, a_case,
                 [LLMResponse(text="I think that's fine.", tool_calls=())])
    assert result.final_status == ExceptionStatus.UNRESOLVED.value
    assert "without calling submit_finding" in result.error


@pytest.mark.db
def test_a_bad_tool_call_is_returned_as_data_not_a_crash(conn, cfg_mod, a_case):
    """A malformed call must let the model correct itself, not kill the batch."""
    bad = LLMResponse(
        tool_calls=(ToolCall(name="get_case_bundle", args={"payment_id": "not-an-id"}),),
        raw_model_turn={"role": "model", "parts": []},
    )
    result = run(conn, cfg_mod, a_case, [bad, finding(evidence=[
        {"record_type": "fee", "record_id": a_case["fee_id"],
         "amount_paise": a_case["delta"], "note": "recovered"},
    ])])
    tool_step = next(s for s in result.steps if s.step_type == "tool_call")
    assert "error" in tool_step.tool_result
    assert result.final_status == ExceptionStatus.RESOLVED.value


# --------------------------------------------------------------------------
# The tool surface
# --------------------------------------------------------------------------


@pytest.mark.db
def test_tools_reject_malformed_and_unknown_identifiers(conn, cfg_mod):
    ctx = ToolContext(conn=conn, cfg=cfg_mod, as_of=AS_OF)
    with pytest.raises(ToolError, match="must look like"):
        run_tool(ctx, "get_case_bundle", {"payment_id": "'; DROP TABLE ops.payments;--"})
    with pytest.raises(ToolError, match="must start with 'pay_'"):
        run_tool(ctx, "get_case_bundle", {"payment_id": "setl_aaaaaaaaaa"})
    with pytest.raises(ToolError, match="no payment with id"):
        run_tool(ctx, "get_case_bundle", {"payment_id": "pay_doesnotexist9"})


@pytest.mark.db
def test_unknown_tools_and_stray_arguments_are_refused(conn, cfg_mod, a_case):
    ctx = ToolContext(conn=conn, cfg=cfg_mod, as_of=AS_OF)
    with pytest.raises(ToolError, match="no tool named"):
        run_tool(ctx, "run_sql", {"query": "select 1"})
    with pytest.raises(ToolError, match="unexpected arguments"):
        run_tool(ctx, "get_case_bundle",
                 {"payment_id": a_case["payment_id"], "limit": 5})


@pytest.mark.db
def test_the_agent_role_cannot_reach_ground_truth_through_a_tool(conn):
    """The quarantine, verified on the connection the tools actually use."""
    with pytest.raises(Exception) as exc:
        conn.execute(sa.text("SELECT * FROM gt.case_truth"))
    assert "permission denied" in str(exc.value).lower()


@pytest.mark.db
def test_the_bundle_answers_a_whole_case_in_one_call(conn, cfg_mod, a_case):
    """Bundling is a quota constraint, not a style choice: six round-trips per
    investigation would exhaust a 500-request day after 83 cases."""
    ctx = ToolContext(conn=conn, cfg=cfg_mod, as_of=AS_OF)
    bundle = run_tool(ctx, "get_case_bundle", {"payment_id": a_case["payment_id"]})
    assert bundle["payment"]["payment_id"] == a_case["payment_id"]
    for key in ("fee", "refunds", "adjustments", "settlement_lines", "reconciliation"):
        assert key in bundle
    assert bundle["reconciliation"]["delta"]["paise"] == a_case["delta"]


def test_every_declared_tool_is_implemented_or_terminal():
    from backend.agents.tools import TOOL_FUNCTIONS

    declared = {s["name"] for s in TOOL_SCHEMAS}
    assert declared == set(TOOL_FUNCTIONS) | {SUBMIT_FINDING}


# --------------------------------------------------------------------------
# Audit trail
# --------------------------------------------------------------------------


@pytest.mark.db
def test_the_whole_trace_is_persisted(conn, cfg_mod, a_case):
    result = run(conn, cfg_mod, a_case, [
        LLMResponse(
            tool_calls=(ToolCall(name="get_case_bundle",
                                 args={"payment_id": a_case["payment_id"]}),),
            raw_model_turn={"role": "model", "parts": []},
        ),
        finding(evidence=[{"record_type": "fee", "record_id": a_case["fee_id"],
                           "amount_paise": a_case["delta"], "note": "fee"}]),
    ])
    persist(conn, result)

    stored = conn.execute(
        sa.text("SELECT * FROM recon.investigations WHERE investigation_id = :i"),
        {"i": result.investigation_id},
    ).mappings().one()
    assert stored["mode"] == "AI"
    assert stored["final_status"] == ExceptionStatus.RESOLVED.value
    assert stored["unexplained_amount"] == 0
    assert stored["prompt_version"]

    steps = conn.execute(
        sa.text(
            "SELECT step_type, tool_name FROM recon.investigation_steps"
            " WHERE investigation_id = :i ORDER BY seq"
        ),
        {"i": result.investigation_id},
    ).all()
    assert [s.step_type for s in steps] == ["llm_call", "tool_call", "llm_call", "finding"]
    assert any(s.tool_name == "get_case_bundle" for s in steps)


@pytest.mark.db
def test_rejected_evidence_is_recorded_not_silently_dropped(conn, cfg_mod, a_case):
    """A finance team must be able to see what the model claimed and why it
    was thrown out."""
    result = run(conn, cfg_mod, a_case, [finding(evidence=[
        {"record_type": "refund", "record_id": "rfnd_INVENTED0001",
         "amount_paise": a_case["delta"], "note": "made up"},
    ])])
    persist(conn, result)
    rows = conn.execute(
        sa.text(
            "SELECT role, note FROM recon.evidence WHERE investigation_id = :i"
        ),
        {"i": result.investigation_id},
    ).all()
    assert any(r.role == "CONTRADICTS" and "REJECTED" in r.note for r in rows)


@pytest.mark.db
def test_the_agent_role_cannot_rewrite_its_own_audit_trail(conn, cfg_mod, a_case):
    result = run(conn, cfg_mod, a_case, [finding()])
    persist(conn, result)
    with pytest.raises(Exception) as exc:
        conn.execute(
            sa.text("UPDATE recon.investigation_steps SET observation = 'edited'")
        )
    assert "permission denied" in str(exc.value).lower()


@pytest.mark.db
def test_investigation_ids_are_deterministic(conn, cfg_mod, a_case):
    a = run(conn, cfg_mod, a_case, [finding()])
    b = run(conn, cfg_mod, a_case, [finding()])
    assert a.investigation_id == b.investigation_id


# --------------------------------------------------------------------------
# Quota machinery
# --------------------------------------------------------------------------


def test_the_cache_returns_a_stored_response_without_calling_out(tmp_path):
    cache = ResponseCache(directory=tmp_path, enabled=True)
    key = cache.key("m", {"contents": [{"text": "hello"}]})
    assert cache.get(key) is None
    cache.put(key, {"candidates": [{"content": {"parts": [{"text": "hi"}]}}]})
    assert cache.get(key)["candidates"]
    assert cache.hits == 1


def test_cache_keys_track_the_request_exactly(tmp_path):
    cache = ResponseCache(directory=tmp_path)
    a = cache.key("m", {"contents": [{"text": "one"}]})
    assert a == cache.key("m", {"contents": [{"text": "one"}]})
    assert a != cache.key("m", {"contents": [{"text": "two"}]})
    assert a != cache.key("other-model", {"contents": [{"text": "one"}]})


def test_the_limiter_spaces_requests_under_the_ceiling():
    limiter = RateLimiter(rpm=600)  # 0.1s apart
    limiter.wait()
    assert limiter.wait() >= 0.0
    assert abs(limiter._min_interval - 0.1) < 1e-9
