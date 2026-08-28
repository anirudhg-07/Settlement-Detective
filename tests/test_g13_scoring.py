"""G13 - the confidence layer.

Two numbers in this system look like confidence, and only one may decide
anything. The model's self-assessment is recorded and ignored; the evidence
score is computed from facts and routes the case.

That is not a stylistic preference. In the live run the model reported 100%
certainty on a case that scored 0 - it was wrong and sure of it. Any design
that lets that number reach a financial decision is broken.
"""

from __future__ import annotations

import inspect

import pytest

from backend.agents.evidence import Citation, build_package
from backend.agents.scoring import (
    CONSISTENT_EVIDENCE,
    MAX_SCORE,
    band_for,
    score_investigation,
)
from backend.config import FinancialConfig
from backend.enums import ExceptionStatus, ExceptionType

DELTA = -28_634


@pytest.fixture
def cfg() -> FinancialConfig:
    return FinancialConfig()


def pkg(citations, delta=DELTA, cause=ExceptionType.FEE_MISMATCH.value):
    return build_package(citations, payment_id="pay_1", delta=delta, cause_type=cause)


def verified(record_type="fee", amount=DELTA, record_id="fee_1"):
    return Citation(record_type, record_id, amount, "note", verified=True)


def rejected(reason="no such record"):
    return Citation("refund", "rfnd_x", 999, "note", verified=False, reason=reason)


def score(package, cause=ExceptionType.FEE_MISMATCH.value, cfg=None, **kw):
    return score_investigation(
        package=package, cause_type=cause,
        declared_unresolved=kw.pop("declared_unresolved", False),
        cfg=cfg or FinancialConfig(), **kw,
    )


# --------------------------------------------------------------------------
# The separation that matters
# --------------------------------------------------------------------------


def test_the_scorer_cannot_see_the_models_confidence():
    """Structural guarantee, not a convention.

    `reasoning_confidence` is not a parameter, so no future edit can quietly
    start weighting a number the model made up about itself.
    """
    params = set(inspect.signature(score_investigation).parameters)
    assert "reasoning_confidence" not in params
    assert "confidence" not in params


def test_identical_evidence_scores_identically_however_sure_the_model_was():
    a = score(pkg([verified()]))
    b = score(pkg([verified()]))
    assert a.score == b.score == MAX_SCORE


# --------------------------------------------------------------------------
# Disqualifying conditions
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs,cause,expected_band",
    [
        ({"error": "LLM unavailable: 503"}, ExceptionType.FEE_MISMATCH.value,
         ExceptionStatus.UNRESOLVED),
        ({"declared_unresolved": True}, ExceptionType.FEE_MISMATCH.value,
         ExceptionStatus.ESCALATED),
    ],
)
def test_failure_and_refusal_score_zero(kwargs, cause, expected_band):
    result = score(pkg([verified()]), cause=cause, **kwargs)
    assert result.score == 0
    assert result.band is expected_band


def test_a_conclusion_with_nothing_verified_scores_zero():
    result = score(pkg([rejected()]))
    assert result.score == 0
    assert result.band is ExceptionStatus.ESCALATED
    assert "no verified evidence" in result.factors[0].name


def test_an_unknown_cause_scores_zero_however_well_the_arithmetic_closed():
    """"Resolved as unknown" is a contradiction; a case with no identified
    cause belongs with a human."""
    result = score(pkg([verified()]), cause=ExceptionType.UNKNOWN_DISCREPANCY.value)
    assert result.score == 0
    assert result.band is ExceptionStatus.ESCALATED


# --------------------------------------------------------------------------
# Graded deductions
# --------------------------------------------------------------------------


def test_fully_accounted_evidence_scores_full_marks():
    result = score(pkg([verified()]))
    assert result.score == MAX_SCORE
    assert result.band is ExceptionStatus.RESOLVED
    assert result.deductions == ()


def test_an_unexplained_remainder_costs_more_the_larger_it_is():
    small = score(pkg([verified(amount=DELTA + 1_000)]))
    large = score(pkg([verified(amount=DELTA // 4)]))
    assert small.score > large.score
    assert all("not fully accounted" in f.name for f in small.deductions)


def test_a_leftover_can_never_be_rounded_away_into_a_resolution():
    result = score(pkg([verified(amount=DELTA // 2)]))
    assert result.band is not ExceptionStatus.RESOLVED


def test_citations_that_did_not_hold_up_cost_points():
    clean = score(pkg([verified()]))
    messy = score(pkg([verified(), rejected(), rejected()]))
    assert messy.score < clean.score
    assert any("did not hold up" in f.name for f in messy.deductions)


def test_a_cause_unsupported_by_the_cited_records_costs_points():
    """FEE_MISMATCH evidenced only by a refund is internally inconsistent."""
    result = score(pkg([verified(record_type="refund", record_id="rfnd_1")]))
    assert any("does not match the evidence" in f.name for f in result.deductions)
    assert result.band is not ExceptionStatus.RESOLVED


def test_incomplete_underlying_records_cost_points():
    result = score(pkg([verified()]), data_flags=("MISSING_FEE_RECORD",))
    assert result.score < MAX_SCORE
    assert any("incomplete" in f.name for f in result.deductions)


def test_exhausting_the_tool_budget_costs_points():
    """An agent that only just got there is less trustworthy than one that
    reached the same answer in two calls."""
    result = score(pkg([verified()]), tool_calls=8, max_tool_calls=8)
    assert result.score == 90
    assert any("tool budget" in f.name for f in result.deductions)


def test_deductions_never_drive_the_score_below_zero():
    result = score(
        pkg([verified(record_type="refund", record_id="rfnd_1", amount=DELTA // 8),
             rejected(), rejected(), rejected()]),
        data_flags=("A", "B", "C"), tool_calls=8, max_tool_calls=8,
    )
    assert 0 <= result.score <= MAX_SCORE


# --------------------------------------------------------------------------
# Bands
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [(100, ExceptionStatus.RESOLVED), (90, ExceptionStatus.RESOLVED),
     (89, ExceptionStatus.REVIEW), (60, ExceptionStatus.REVIEW),
     (59, ExceptionStatus.ESCALATED), (0, ExceptionStatus.ESCALATED)],
)
def test_band_boundaries_are_inclusive_at_the_thresholds(value, expected, cfg):
    assert band_for(value, cfg) is expected


def test_thresholds_come_from_config_not_from_constants():
    strict = FinancialConfig(evidence_auto_resolve=99, evidence_review_min=95)
    assert band_for(98, strict) is ExceptionStatus.REVIEW
    assert band_for(94, strict) is ExceptionStatus.ESCALATED


# --------------------------------------------------------------------------
# Auditability
# --------------------------------------------------------------------------


def test_the_score_explains_itself():
    """A single opaque number is not auditable; a list of deductions is."""
    result = score(pkg([verified(amount=DELTA // 2), rejected()]))
    lines = result.explain()
    assert "starting score" in lines[0]
    assert any("not fully accounted" in line for line in lines)
    assert str(result.score) in lines[-1]


def test_every_deduction_carries_a_reason():
    result = score(pkg([verified(amount=DELTA // 3), rejected()]),
                   data_flags=("MISSING_FEE_RECORD",))
    assert result.deductions
    assert all(f.name and f.detail for f in result.deductions)


def test_every_taxonomy_cause_except_unknown_declares_its_evidence_types():
    covered = set(CONSISTENT_EVIDENCE)
    everything = {t.value for t in ExceptionType}
    assert everything - covered == {ExceptionType.UNKNOWN_DISCREPANCY.value}
