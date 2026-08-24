"""G7 - the synthetic data generator.

The contract: a clean world reconciles at 100%. Anything less is a generator
bug, and it must be caught here - once Phase 4 injects deliberate faults, a
generator bug is indistinguishable from an exception.
"""

from __future__ import annotations

from datetime import date

import pytest
import sqlalchemy as sa

from backend.config import FinancialConfig
from backend.enums import (
    PaymentMethod,
    PaymentStatus,
    RefundStatus,
    SettlementItemType,
)
from backend.generation.generator import generate_world
from backend.generation.profile import MerchantProfile
from backend.generation.verify import batch_residuals, reconcile_world
from backend.reconciliation.fees import compute_fee_and_tax
from backend.reconciliation.timing import settlement_eligible_on

AS_OF = date(2026, 1, 31)


@pytest.fixture(scope="module")
def cfg_mod() -> FinancialConfig:
    return FinancialConfig()


@pytest.fixture(scope="module")
def world(cfg_mod):
    """One 3,000-payment world, shared across the module."""
    return generate_world(seed=7, n_payments=3_000, as_of=AS_OF, cfg=cfg_mod)


# --- the headline contract ------------------------------------------------
def test_clean_world_reconciles_completely(world, cfg_mod):
    result = reconcile_world(world, cfg_mod, AS_OF)
    assert result["mismatches"] == []
    assert result["match_rate_bps"] == 10_000


def test_every_batch_balances_exactly(world):
    """No tolerance: a batch payout IS the sum of its lines."""
    assert batch_residuals(world) == []


# --- reproducibility ------------------------------------------------------
def test_same_seed_produces_an_identical_world(cfg_mod):
    a = generate_world(seed=99, n_payments=300, as_of=AS_OF, cfg=cfg_mod)
    b = generate_world(seed=99, n_payments=300, as_of=AS_OF, cfg=cfg_mod)
    assert a.payments == b.payments
    assert a.settlement_items == b.settlement_items
    assert a.stats == b.stats


def test_different_seeds_produce_different_worlds(cfg_mod):
    a = generate_world(seed=1, n_payments=300, as_of=AS_OF, cfg=cfg_mod)
    b = generate_world(seed=2, n_payments=300, as_of=AS_OF, cfg=cfg_mod)
    assert a.payments != b.payments


# --- financial coherence of every row -------------------------------------
def test_every_fee_matches_the_schedule(world, cfg_mod):
    payments = {p["payment_id"]: p for p in world.payments}
    for fee in world.fees:
        payment = payments[fee["payment_id"]]
        expected = compute_fee_and_tax(
            payment["amount"], PaymentMethod(payment["payment_method"]), cfg_mod
        )
        assert (fee["fee_amount"], fee["tax_amount"]) == (expected.fee, expected.tax)


def test_failed_payments_have_no_fee_and_no_settlement(world):
    failed = {
        p["payment_id"]
        for p in world.payments
        if p["status"] == PaymentStatus.FAILED.value
    }
    assert failed, "the profile should produce some failed payments"
    assert not [f for f in world.fees if f["payment_id"] in failed]
    assert not [i for i in world.settlement_items if i["payment_id"] in failed]


def test_no_payment_is_over_refunded(world):
    amounts = {p["payment_id"]: p["amount"] for p in world.payments}
    totals: dict[str, int] = {}
    for r in world.refunds:
        if r["status"] != RefundStatus.FAILED.value:
            totals[r["payment_id"]] = totals.get(r["payment_id"], 0) + r["amount"]
    assert all(total <= amounts[pid] for pid, total in totals.items())


def test_every_amount_is_a_whole_number_of_paise(world):
    for collection in (world.payments, world.orders, world.refunds):
        assert all(isinstance(row.get("amount", row.get("order_amount")), int) for row in collection)


def test_all_currency_is_inr(world):
    assert {o["currency"] for o in world.orders} == {"INR"}
    assert {p["currency"] for p in world.payments} == {"INR"}


def test_refund_status_matches_payment_status(world):
    """A payment marked refunded must actually have refunds behind it."""
    processed: dict[str, int] = {}
    for r in world.refunds:
        if r["status"] == RefundStatus.PROCESSED.value:
            processed[r["payment_id"]] = processed.get(r["payment_id"], 0) + r["amount"]
    for p in world.payments:
        total = processed.get(p["payment_id"], 0)
        if p["status"] == PaymentStatus.CAPTURED.value:
            assert total == 0
        elif p["status"] == PaymentStatus.PARTIALLY_REFUNDED.value:
            assert 0 < total < p["amount"]
        elif p["status"] == PaymentStatus.REFUNDED.value:
            assert total >= p["amount"]


# --- timing ---------------------------------------------------------------
def test_nothing_settles_before_its_cycle_or_after_the_as_of_date(world, cfg_mod):
    settlements = {s["settlement_id"]: s for s in world.settlements}
    assert all(
        s["settlement_date"].date() <= AS_OF for s in world.settlements
    ), "a batch cannot exist in the future"

    payments = {p["payment_id"]: p for p in world.payments}
    for item in world.settlement_items:
        if item["item_type"] != SettlementItemType.PAYMENT.value:
            continue
        payment = payments[item["payment_id"]]
        settled_on = settlements[item["settlement_id"]]["settlement_date"].date()
        assert settled_on == settlement_eligible_on(payment["captured_at"], cfg_mod)


def test_recent_payments_are_pending_not_missing(world, cfg_mod):
    """Payments inside the settlement window must not read as exceptions."""
    result = reconcile_world(world, cfg_mod, AS_OF)
    assert result["counts"].get("PENDING_SETTLEMENT", 0) > 0
    assert "EXCEPTION" not in result["counts"]


# --- ground truth ---------------------------------------------------------
def test_ground_truth_covers_every_payment_and_is_clean(world):
    assert len(world.truths) == len(world.payments)
    assert {t["payment_id"] for t in world.truths} == {
        p["payment_id"] for p in world.payments
    }
    assert all(t["is_exception"] is False for t in world.truths)
    assert all(t["reason_code"] is None for t in world.truths)


# --- the profile is actually respected ------------------------------------
def test_distributions_follow_the_merchant_profile(world):
    profile = MerchantProfile()
    total = len(world.payments)
    mix = world.stats["method_mix"]
    # UPI-dominant, as configured.
    assert mix["upi"] > mix["card"] > mix["netbanking"]
    failure_rate_bps = world.stats["failed_payments"] * 10_000 // total
    assert abs(failure_rate_bps - profile.payment_failure_bps) < 250


def test_identifiers_are_unique_and_prefixed(world):
    ids = [p["payment_id"] for p in world.payments]
    assert len(set(ids)) == len(ids)
    assert all(i.startswith("pay_") for i in ids)
    assert all(s["settlement_id"].startswith("setl_") for s in world.settlements)


# --- database round-trip --------------------------------------------------
@pytest.mark.db
def test_world_loads_into_postgres_and_survives_every_constraint(cfg_mod):
    """Rolled back afterwards, so a loaded dataset is never destroyed."""
    from backend.db.session import owner_engine
    from backend.generation.persist import persist

    try:
        engine = owner_engine()
        with engine.connect() as probe:
            probe.execute(sa.text("SELECT 1"))
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"PostgreSQL unavailable ({exc})")

    small = generate_world(seed=5, n_payments=400, as_of=AS_OF, cfg=cfg_mod)
    with engine.connect() as conn:
        tx = conn.begin()
        try:
            written = persist(small, conn, truncate=True)
            assert written["payments"] == 400
            assert written["gt.case_truth"] == 400
            for table, key in (
                ("ops.payments", "payments"),
                ("ops.settlement_items", "settlement_items"),
                ("gt.case_truth", "truths"),
            ):
                count = conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar()
                assert count == len(getattr(small, key))
            # The batch invariant, asserted by the database rather than in memory.
            residual = conn.execute(
                sa.text(
                    "SELECT count(*) FROM ops.settlements s "
                    "WHERE s.net_amount <> ("
                    "  SELECT COALESCE(sum(i.net_amount), 0) FROM ops.settlement_items i"
                    "  WHERE i.settlement_id = s.settlement_id)"
                )
            ).scalar()
            assert residual == 0
        finally:
            tx.rollback()
