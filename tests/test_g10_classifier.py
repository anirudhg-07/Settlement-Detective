"""G10 - exception classification, the deterministic baseline.

This baseline is deliberately strong. It is what the AI must beat in Phase 14,
and a weak one would make that comparison worthless. These tests hold it to the
standard the evaluation quotes - and pin its one honest blind spot in place, so
nobody can quietly widen or narrow the AI's apparent gain.
"""

from __future__ import annotations

from datetime import date

import pytest
import sqlalchemy as sa

from backend.config import FinancialConfig, get_settings
from backend.enums import ExceptionType
from backend.reconciliation.classifier import (
    FAMILY_TO_TYPE,
    build_hypotheses,
    classify,
    classify_run,
    find_duplicate_payments,
    find_late_settlements,
    find_unauthorised_adjustments,
    load_context,
)
from backend.reconciliation.engine import run_reconciliation

AS_OF = date(2026, 1, 31)


@pytest.fixture(scope="module")
def cfg_mod() -> FinancialConfig:
    return get_settings().financial()


@pytest.fixture
def conn():
    from backend.db.session import owner_engine

    try:
        engine = owner_engine()
        with engine.connect() as probe:
            probe.execute(sa.text("SELECT 1"))
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"PostgreSQL unavailable ({exc})")
    with engine.connect() as connection:
        tx = connection.begin()
        try:
            if connection.execute(
                sa.text("SELECT count(*) FROM ops.payments")
            ).scalar() == 0:
                pytest.skip("no dataset loaded; run scripts/generate_data.py")
            yield connection
        finally:
            tx.rollback()


@pytest.fixture
def classified(conn, cfg_mod):
    summary = run_reconciliation(conn, cfg=cfg_mod, as_of=AS_OF, commit=False)
    result = classify_run(conn, run_id=summary.run_id, cfg=cfg_mod, as_of=AS_OF)
    return summary, result


def _truth(conn) -> dict[str, str]:
    return {
        r.payment_id: r.reason_code
        for r in conn.execute(
            sa.text("SELECT payment_id, reason_code FROM gt.case_truth WHERE is_exception")
        )
    }


def _assigned(conn, run_id) -> dict[str, str]:
    return {
        r.payment_id: r.exception_type
        for r in conn.execute(
            sa.text(
                "SELECT payment_id, exception_type FROM recon.exceptions WHERE run_id = :r"
            ),
            {"r": run_id},
        )
    }


# --- coverage -------------------------------------------------------------
@pytest.mark.db
def test_every_injected_exception_is_now_detected(conn, classified):
    """Rules close the gap arithmetic structurally cannot."""
    summary, _ = classified
    assert set(_truth(conn)) <= set(_assigned(conn, summary.run_id))


@pytest.mark.db
def test_still_no_false_positives(conn, classified):
    """The rules must not start flagging healthy payments to raise recall."""
    summary, _ = classified
    assert set(_assigned(conn, summary.run_id)) - set(_truth(conn)) == set()


@pytest.mark.db
def test_every_exception_gets_a_type(conn, classified):
    summary, _ = classified
    untyped = conn.execute(
        sa.text(
            "SELECT count(*) FROM recon.exceptions"
            " WHERE run_id = :r AND exception_type IS NULL"
        ),
        {"r": summary.run_id},
    ).scalar()
    assert untyped == 0


# --- accuracy -------------------------------------------------------------
@pytest.mark.db
def test_no_exception_is_given_the_wrong_type(conn, classified):
    """A wrong label is worse than no label: it sends a human down a dead end."""
    summary, _ = classified
    assigned, truth = _assigned(conn, summary.run_id), _truth(conn)
    wrong = []
    for pid, reason in truth.items():
        want = FAMILY_TO_TYPE.get(reason, reason)
        if want is None or pid not in assigned:
            continue
        expected = want.value if hasattr(want, "value") else want
        if assigned[pid] != expected:
            wrong.append((pid, reason, assigned[pid]))
    assert wrong == []


@pytest.mark.db
def test_multi_cause_is_declined_rather_than_guessed(conn, classified):
    """The baseline's honest blind spot, pinned in place.

    Two faults share one discrepancy, so no single cause matches it. Answering
    anyway would mean guessing. Reporting UNKNOWN is the correct behaviour, and
    it is precisely the gap an investigating agent exists to close - so this
    test guards it from being quietly widened or narrowed later.
    """
    summary, _ = classified
    assigned, truth = _assigned(conn, summary.run_id), _truth(conn)
    multi = [pid for pid, reason in truth.items() if reason == "MULTI_CAUSE"]
    assert multi
    assert all(
        assigned[pid] == ExceptionType.UNKNOWN_DISCREPANCY.value for pid in multi
    )


@pytest.mark.db
def test_genuinely_unknown_cases_stay_unknown(conn, classified):
    """No record explains these. The baseline must not invent one."""
    summary, _ = classified
    assigned, truth = _assigned(conn, summary.run_id), _truth(conn)
    unknowns = [pid for pid, r in truth.items() if r == "UNKNOWN_DISCREPANCY"]
    assert unknowns
    assert all(
        assigned[pid] == ExceptionType.UNKNOWN_DISCREPANCY.value for pid in unknowns
    )


@pytest.mark.db
def test_multi_cause_leaves_a_detectable_signature(conn, classified):
    """Several partial causes summing to the delta - the agent's starting point."""
    _, result = classified
    assert result.combination_cases > 0


# --- the discriminations that matter --------------------------------------
@pytest.mark.db
def test_a_scheduled_refund_is_called_timing_not_missing(conn, classified):
    """Same delta, different truth. Getting this wrong sends a healthy case
    to a human as a suspected loss."""
    summary, _ = classified
    assigned, truth = _assigned(conn, summary.run_id), _truth(conn)
    shifted = [pid for pid, r in truth.items() if r == "TIMING_SHIFTED"]
    missing = [pid for pid, r in truth.items() if r == "MISSING_REFUND"]
    assert shifted and missing
    assert all(
        assigned[pid] == ExceptionType.SETTLEMENT_TIMING.value for pid in shifted
    )
    assert all(assigned[pid] == ExceptionType.MISSING_REFUND.value for pid in missing)


@pytest.mark.db
def test_an_unlinked_batch_adjustment_is_found(conn, classified):
    """CROSS_ENTITY: reachable only via the batch, never via payment_id."""
    summary, _ = classified
    assigned, truth = _assigned(conn, summary.run_id), _truth(conn)
    cross = [pid for pid, r in truth.items() if r == "CROSS_ENTITY"]
    assert cross
    assert all(
        assigned[pid] == ExceptionType.UNEXPECTED_ADJUSTMENT.value for pid in cross
    )


@pytest.mark.db
def test_ambiguous_attribution_is_refused(conn, cfg_mod):
    """Two identical unlinked adjustments in one batch is a coin toss, not a
    classification - so the hypothesis is not offered at all."""
    ctx = load_context(conn)
    for batch, adjustments in ctx.unlinked_by_batch.items():
        if len(adjustments) < 2:
            continue
        amount = adjustments[0]["amount"]
        adjustments.append(dict(adjustments[0], adjustment_id="adj_twin", amount=amount))
        payment_id = next(
            (
                pid
                for pid, lines in ctx.payment_lines.items()
                if any(l["settlement_id"] == batch for l in lines)
            ),
            None,
        )
        if not payment_id:
            continue
        offered = [
            h
            for h in build_hypotheses(payment_id, ctx, cfg_mod, AS_OF)
            if h.exception_type is ExceptionType.UNEXPECTED_ADJUSTMENT
            and h.explains == amount
        ]
        assert offered == []
        return
    pytest.skip("no batch with multiple unlinked adjustments in this dataset")


# --- the rules ------------------------------------------------------------
@pytest.mark.db
def test_duplicate_rule_flags_the_later_charge(conn):
    ctx = load_context(conn)
    hits = find_duplicate_payments(ctx)
    assert hits
    for hit in hits:
        later = ctx.payments[hit["payment_id"]]
        first = ctx.payments[hit["evidence"]["duplicate_of"]]
        assert later["captured_at"] >= first["captured_at"]
        assert later["amount"] == first["amount"]
        assert later["order_id"] == first["order_id"]


@pytest.mark.db
def test_late_settlement_rule_only_fires_past_the_deadline(conn, cfg_mod):
    from backend.reconciliation.timing import settlement_deadline

    ctx = load_context(conn)
    hits = find_late_settlements(ctx, cfg_mod)
    assert hits
    for hit in hits:
        payment = ctx.payments[hit["payment_id"]]
        assert date.fromisoformat(hit["evidence"]["settled_on"]) > settlement_deadline(
            payment["captured_at"], cfg_mod
        )
        assert hit["evidence"]["days_late"] > 0


@pytest.mark.db
def test_unauthorised_adjustment_rule_requires_a_missing_reason(conn):
    ctx = load_context(conn)
    hits = find_unauthorised_adjustments(ctx)
    assert hits
    ids = {h["evidence"]["adjustment_id"] for h in hits}
    for adjustments in ctx.adjustments.values():
        for adjustment in adjustments:
            if adjustment["reason"]:
                assert adjustment["adjustment_id"] not in ids


@pytest.mark.db
def test_rules_never_duplicate_an_arithmetic_exception(conn, classified):
    """A payment that already fails arithmetically is one case, not two."""
    summary, _ = classified
    duplicated = conn.execute(
        sa.text(
            "SELECT payment_id FROM recon.exceptions WHERE run_id = :r"
            " GROUP BY payment_id HAVING count(*) > 1"
        ),
        {"r": summary.run_id},
    ).all()
    assert duplicated == []


@pytest.mark.db
def test_rule_exceptions_are_marked_as_such(conn, classified):
    summary, _ = classified
    rows = conn.execute(
        sa.text(
            "SELECT detected_by, count(*) FROM recon.exceptions"
            " WHERE run_id = :r GROUP BY detected_by"
        ),
        {"r": summary.run_id},
    ).all()
    by = dict(rows)
    assert by["RULE"] > 0 and by["RESIDUAL"] > 0


@pytest.mark.db
def test_classification_is_deterministic(conn, cfg_mod):
    ctx = load_context(conn)
    pid, delta = conn.execute(
        sa.text(
            "SELECT payment_id, delta FROM recon.recon_results"
            " WHERE status = 'EXCEPTION' LIMIT 1"
        )
    ).one_or_none() or (None, None)
    if pid is None:
        pytest.skip("no exceptions available")
    a = classify(pid, delta, ctx, cfg_mod, AS_OF)
    b = classify(pid, delta, ctx, cfg_mod, AS_OF)
    assert (a.exception_type, a.explains) == (b.exception_type, b.explains)
