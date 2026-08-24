"""G9 - the deterministic reconciliation engine.

Every test here runs inside a transaction that is rolled back, so the suite
never leaves stray runs behind or disturbs a loaded dataset.

The engine is the baseline the AI must beat in Phase 14. A weak baseline would
make that comparison meaningless, so these tests hold it to the standard the
evaluation will quote: no false positives, and everything arithmetic can see.
"""

from __future__ import annotations

from datetime import date

import pytest
import sqlalchemy as sa

from backend.config import FinancialConfig, get_settings
from backend.enums import ReconStatus
from backend.reconciliation.engine import (
    load_dataset,
    reconcile_dataset,
    run_reconciliation,
)

AS_OF = date(2026, 1, 31)


@pytest.fixture(scope="module")
def cfg_mod() -> FinancialConfig:
    return get_settings().financial()


@pytest.fixture
def conn():
    """A connection whose transaction is always rolled back."""
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
def summary(conn, cfg_mod):
    return run_reconciliation(conn, cfg=cfg_mod, as_of=AS_OF, commit=False)


# --- the run is fully audited --------------------------------------------
@pytest.mark.db
def test_run_is_recorded_with_the_config_it_used(conn, summary, cfg_mod):
    """A historical result must always be explainable by its parameters."""
    row = conn.execute(
        sa.text("SELECT * FROM recon.recon_runs WHERE run_id = :r"),
        {"r": summary.run_id},
    ).mappings().one()
    assert row["as_of_date"] == AS_OF
    assert row["tolerance_paise"] == cfg_mod.tolerance_paise
    assert row["config_snapshot"] == cfg_mod.snapshot()
    assert row["completed_at"] is not None
    assert row["records_processed"] == summary.records_processed


@pytest.mark.db
def test_run_totals_match_the_result_rows(conn, summary):
    """The denormalised totals the dashboard reads must not drift."""
    row = conn.execute(
        sa.text(
            "SELECT matched_count, pending_count, exception_count"
            "  FROM recon.recon_runs WHERE run_id = :r"
        ),
        {"r": summary.run_id},
    ).mappings().one()
    actual = dict(
        conn.execute(
            sa.text(
                "SELECT status, count(*) FROM recon.recon_results"
                " WHERE run_id = :r GROUP BY status"
            ),
            {"r": summary.run_id},
        ).all()
    )
    assert row["matched_count"] == actual.get(ReconStatus.MATCHED.value, 0)
    assert row["pending_count"] == actual.get(ReconStatus.PENDING_SETTLEMENT.value, 0)
    assert row["exception_count"] == actual.get(ReconStatus.EXCEPTION.value, 0)


@pytest.mark.db
def test_exactly_one_result_per_payment(conn, summary):
    payments = conn.execute(sa.text("SELECT count(*) FROM ops.payments")).scalar()
    results = conn.execute(
        sa.text("SELECT count(*) FROM recon.recon_results WHERE run_id = :r"),
        {"r": summary.run_id},
    ).scalar()
    assert results == payments == summary.records_processed


@pytest.mark.db
def test_an_exception_row_exists_for_every_exception_result(conn, summary):
    counts = conn.execute(
        sa.text(
            "SELECT (SELECT count(*) FROM recon.recon_results"
            "          WHERE run_id = :r AND status = 'EXCEPTION') AS results,"
            "       (SELECT count(*) FROM recon.exceptions WHERE run_id = :r) AS excs"
        ),
        {"r": summary.run_id},
    ).mappings().one()
    assert counts["results"] == counts["excs"] == summary.exceptions_written


@pytest.mark.db
def test_detected_exceptions_are_left_unclassified(conn, summary):
    """Phase 5 detects; Phase 6 types. A placeholder here would be a false label."""
    rows = conn.execute(
        sa.text(
            "SELECT exception_type, detected_by, status FROM recon.exceptions"
            " WHERE run_id = :r"
        ),
        {"r": summary.run_id},
    ).all()
    assert rows
    assert all(r.exception_type is None for r in rows)
    assert all(r.detected_by == "RESIDUAL" for r in rows)
    assert all(r.status == "OPEN" for r in rows)


# --- the arithmetic is right ---------------------------------------------
@pytest.mark.db
def test_persisted_delta_is_always_actual_minus_expected(conn, summary):
    drift = conn.execute(
        sa.text(
            "SELECT count(*) FROM recon.recon_results"
            " WHERE run_id = :r AND delta <> actual_net - expected_net"
        ),
        {"r": summary.run_id},
    ).scalar()
    assert drift == 0


@pytest.mark.db
def test_matched_rows_are_all_inside_tolerance(conn, summary, cfg_mod):
    outside = conn.execute(
        sa.text(
            "SELECT count(*) FROM recon.recon_results"
            " WHERE run_id = :r AND status = 'MATCHED' AND abs(delta) > :tol"
        ),
        {"r": summary.run_id, "tol": cfg_mod.tolerance_paise},
    ).scalar()
    assert outside == 0


@pytest.mark.db
def test_exception_rows_are_all_outside_tolerance(conn, summary, cfg_mod):
    inside = conn.execute(
        sa.text(
            "SELECT count(*) FROM recon.exceptions"
            " WHERE run_id = :r AND abs(delta) <= :tol"
        ),
        {"r": summary.run_id, "tol": cfg_mod.tolerance_paise},
    ).scalar()
    assert inside == 0


@pytest.mark.db
def test_engine_is_deterministic(conn, cfg_mod):
    """Same data, same as-of date, same verdict - twice."""
    a = reconcile_dataset(load_dataset(conn), cfg_mod, AS_OF)
    b = reconcile_dataset(load_dataset(conn), cfg_mod, AS_OF)
    assert a == b


@pytest.mark.db
def test_as_of_date_changes_the_answer(conn, cfg_mod):
    """Reconciliation is a statement about a moment, not a timeless fact."""
    early = reconcile_dataset(load_dataset(conn), cfg_mod, date(2025, 12, 1))
    late = reconcile_dataset(load_dataset(conn), cfg_mod, AS_OF)
    pending_early = sum(1 for o in early if o.status is ReconStatus.PENDING_SETTLEMENT)
    pending_late = sum(1 for o in late if o.status is ReconStatus.PENDING_SETTLEMENT)
    assert pending_early > pending_late


# --- batch-level identity -------------------------------------------------
@pytest.mark.db
def test_all_batches_balance_on_clean_data(summary):
    assert summary.batches_out_of_balance == []
    assert summary.batches_checked > 0


@pytest.mark.db
def test_an_unbalanced_batch_is_caught(conn, cfg_mod):
    """No tolerance at the batch level: a payout IS the sum of its lines."""
    settlement_id = conn.execute(
        sa.text("SELECT settlement_id FROM ops.settlements LIMIT 1")
    ).scalar()
    conn.execute(
        sa.text(
            "UPDATE ops.settlements SET net_amount = net_amount + 1"
            " WHERE settlement_id = :s"
        ),
        {"s": settlement_id},
    )
    result = run_reconciliation(conn, cfg=cfg_mod, as_of=AS_OF, commit=False)
    assert [s for s, _ in result.batches_out_of_balance] == [settlement_id]
    assert result.batches_out_of_balance[0][1] == 1


# --- scored against ground truth -----------------------------------------
@pytest.mark.db
def test_no_false_positives_against_ground_truth(conn, summary):
    """The number that matters most: never flag a healthy payment."""
    spurious = conn.execute(
        sa.text(
            "SELECT count(*) FROM recon.exceptions e"
            "  LEFT JOIN gt.case_truth t USING (payment_id)"
            " WHERE e.run_id = :r AND COALESCE(t.is_exception, false) = false"
        ),
        {"r": summary.run_id},
    ).scalar()
    assert spurious == 0


@pytest.mark.db
def test_every_money_moving_exception_is_found(conn, summary):
    missed = conn.execute(
        sa.text(
            "SELECT count(*) FROM gt.case_truth t"
            " WHERE t.is_exception"
            "   AND COALESCE((t.injection_params->>'delta_visible')::bool, false)"
            "   AND NOT EXISTS (SELECT 1 FROM recon.exceptions e"
            "                    WHERE e.run_id = :r AND e.payment_id = t.payment_id)"
        ),
        {"r": summary.run_id},
    ).scalar()
    assert missed == 0


@pytest.mark.db
def test_the_no_delta_exceptions_are_honestly_missed(conn, summary):
    """A duplicate charge reconciles perfectly. Arithmetic cannot see it.

    Asserting this keeps the baseline honest: it is what Phase 6's rules exist
    to fix, and pretending otherwise would inflate the AI's apparent gain.
    """
    found = conn.execute(
        sa.text(
            "SELECT count(*) FROM gt.case_truth t"
            "  JOIN recon.exceptions e ON e.payment_id = t.payment_id AND e.run_id = :r"
            " WHERE t.is_exception"
            "   AND NOT COALESCE((t.injection_params->>'delta_visible')::bool, false)"
        ),
        {"r": summary.run_id},
    ).scalar()
    assert found == 0


# --- throughput -----------------------------------------------------------
@pytest.mark.db
def test_throughput_is_adequate_for_the_evaluation(summary):
    assert summary.records_processed >= 10_000
    assert summary.throughput > 1_000, f"only {summary.throughput:,.0f} payments/s"
