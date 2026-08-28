"""The confidence layer: an evidence score computed by code.

There are two numbers in this system that look like confidence, and only one
of them may decide anything.

``reasoning_confidence``
    What the model says about itself. Recorded for analysis, **never read** by
    any decision. In testing it reported 100% certainty on cases it had got
    wrong; a number that behaves that way cannot be allowed near a financial
    outcome.

``evidence_score``
    Computed here, from facts: did the citations verify, does the arithmetic
    close, is the stated cause consistent with the records cited, are the
    underlying records intact. This is what routes a case.

The score starts at 100 and loses points for specific, named reasons, because
a reviewer needs to see *why* a case scored what it did. A single opaque number
is not auditable; a list of deductions is.

Some conditions are disqualifying rather than costly - an LLM outage, a model
that declined to answer, a conclusion with nothing verified behind it. Those
drive the score to zero outright. There is no amount of partial credit that
should let them through.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.agents.evidence import EvidencePackage
from backend.config import FinancialConfig
from backend.enums import ExceptionStatus, ExceptionType

#: Record types that could plausibly evidence each cause. A conclusion of
#: FEE_MISMATCH backed only by a refund is internally inconsistent, whatever
#: the arithmetic came to.
CONSISTENT_EVIDENCE: dict[str, frozenset[str]] = {
    ExceptionType.FEE_MISMATCH.value: frozenset({"fee", "settlement_item"}),
    ExceptionType.TAX_MISMATCH.value: frozenset({"fee", "settlement_item"}),
    ExceptionType.MISSING_REFUND.value: frozenset({"refund", "settlement_item"}),
    ExceptionType.INCORRECT_REFUND_AMOUNT.value: frozenset({"refund", "settlement_item"}),
    ExceptionType.UNEXPECTED_ADJUSTMENT.value: frozenset({"adjustment", "settlement_item"}),
    ExceptionType.MISSING_SETTLEMENT.value: frozenset({"payment", "settlement"}),
    ExceptionType.PARTIAL_SETTLEMENT.value: frozenset({"settlement", "settlement_item", "payment"}),
    ExceptionType.SETTLEMENT_TIMING.value: frozenset(
        {"settlement", "settlement_item", "refund", "payment"}
    ),
    ExceptionType.DUPLICATE_PAYMENT.value: frozenset({"payment", "order"}),
}

MAX_SCORE = 100


@dataclass(frozen=True)
class Factor:
    """One named reason the score is what it is."""

    name: str
    delta: int
    detail: str


@dataclass
class EvidenceScore:
    score: int
    band: ExceptionStatus
    factors: tuple[Factor, ...]

    @property
    def deductions(self) -> tuple[Factor, ...]:
        return tuple(f for f in self.factors if f.delta < 0)

    def explain(self) -> list[str]:
        lines = [f"{'starting score':<44}{MAX_SCORE:>6}"]
        for f in self.factors:
            lines.append(f"  {f.name:<42}{f.delta:>+6}   {f.detail}")
        lines.append(f"{'evidence score':<44}{self.score:>6}  -> {self.band.value}")
        return lines


def band_for(score: int, cfg: FinancialConfig) -> ExceptionStatus:
    if score >= cfg.evidence_auto_resolve:
        return ExceptionStatus.RESOLVED
    if score >= cfg.evidence_review_min:
        return ExceptionStatus.REVIEW
    return ExceptionStatus.ESCALATED


def score_investigation(
    *,
    package: EvidencePackage,
    cause_type: str,
    declared_unresolved: bool,
    cfg: FinancialConfig,
    data_flags: tuple[str, ...] = (),
    error: str | None = None,
    tool_calls: int = 0,
    max_tool_calls: int = 8,
) -> EvidenceScore:
    """Score one investigation from what it actually produced."""
    factors: list[Factor] = []

    # -- disqualifying conditions -----------------------------------------
    if error:
        factors.append(Factor("investigation failed", -MAX_SCORE, error[:120]))
        return EvidenceScore(0, ExceptionStatus.UNRESOLVED, tuple(factors))

    if declared_unresolved:
        factors.append(
            Factor("agent declined to conclude", -MAX_SCORE,
                   "reported it could not account for the discrepancy")
        )
        return EvidenceScore(0, ExceptionStatus.ESCALATED, tuple(factors))

    if not package.verified:
        factors.append(
            Factor("no verified evidence", -MAX_SCORE,
                   f"{len(package.rejected)} citation(s) offered, none held up")
        )
        return EvidenceScore(0, ExceptionStatus.ESCALATED, tuple(factors))

    if cause_type == ExceptionType.UNKNOWN_DISCREPANCY.value:
        factors.append(
            Factor("cause is unknown", -MAX_SCORE,
                   "a case with no identified cause belongs with a human")
        )
        return EvidenceScore(0, ExceptionStatus.ESCALATED, tuple(factors))

    # -- graded deductions -------------------------------------------------
    score = MAX_SCORE
    factors.append(
        Factor("evidence verified", 0,
               f"{len(package.verified)} citation(s) matched their records")
    )

    unexplained = abs(package.unexplained)
    if unexplained > cfg.tolerance_paise:
        share = min(1.0, unexplained / max(1, abs(package.delta)))
        penalty = 20 + int(60 * share)
        score -= penalty
        factors.append(
            Factor("discrepancy not fully accounted for", -penalty,
                   f"{unexplained} paise of {abs(package.delta)} unexplained")
        )

    if package.rejected:
        penalty = min(25, 10 * len(package.rejected))
        score -= penalty
        factors.append(
            Factor("citations that did not hold up", -penalty,
                   f"{len(package.rejected)} rejected: "
                   + "; ".join(c.reason or "?" for c in package.rejected[:2])[:100])
        )

    allowed = CONSISTENT_EVIDENCE.get(cause_type)
    if allowed is not None:
        cited = {c.record_type for c in package.verified}
        if not (cited & allowed):
            score -= 20
            factors.append(
                Factor("cause does not match the evidence", -20,
                       f"{cause_type} evidenced only by {sorted(cited)}")
            )

    if data_flags:
        penalty = min(20, 10 * len(data_flags))
        score -= penalty
        factors.append(
            Factor("underlying records are incomplete", -penalty, ", ".join(data_flags))
        )

    if max_tool_calls and tool_calls >= max_tool_calls:
        score -= 10
        factors.append(
            Factor("used the entire tool budget", -10,
                   f"{tool_calls}/{max_tool_calls} calls - it barely got there")
        )

    score = max(0, min(MAX_SCORE, score))
    return EvidenceScore(score, band_for(score, cfg), tuple(factors))
