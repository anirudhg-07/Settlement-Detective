"""G3 - the expected settlement calculation.

Tests 11 and 12 are where the Phase 1 refund decision is pinned down: they
*are* the assumption, expressed executably. Changing the business rule without
changing these tests is impossible.
"""

from __future__ import annotations

from backend.enums import (
    AdjustmentType,
    DataCondition,
    PaymentMethod,
    PaymentStatus,
    RefundStatus,
)
from backend.reconciliation.settlement_math import (
    AdjustmentFact,
    PaymentFacts,
    RefundFact,
    check_fee_schedule,
    expected_net_settlement,
)


def _payment(**overrides) -> PaymentFacts:
    base = dict(
        payment_id="pay_1001",
        amount=100_000,
        method=PaymentMethod.CARD,
        status=PaymentStatus.CAPTURED,
        fee=2_000,
        tax=360,
    )
    base.update(overrides)
    return PaymentFacts(**base)


# --- 10. clean payment ----------------------------------------------------
def test_clean_payment_is_gross_less_fee_and_tax(cfg):
    result = expected_net_settlement(_payment(), cfg)
    assert result.expected_net == 97_640
    assert result.flags == ()
    assert sum(result.components().values()) == result.expected_net


# --- 11. partial refund, fee RETAINED (the Phase 1 decision) --------------
def test_partial_refund_retains_the_fee(cfg):
    """Part C, Case 2: Rs1,000 payment, Rs400 refund -> Rs576.40 net.

    The merchant is out the Rs23.60 fee+GST on the refunded portion. This is
    the `reverse_fee_on_refund = False` decision made executable.
    """
    facts = _payment(
        status=PaymentStatus.PARTIALLY_REFUNDED,
        refunds=(RefundFact("rfnd_9001", 40_000, RefundStatus.PROCESSED),),
    )
    result = expected_net_settlement(facts, cfg)
    assert result.expected_net == 57_640
    assert result.fee_reversed == 0
    assert result.tax_reversed == 0
    assert result.fee_retained == 2_000


# --- 12. the flag genuinely flips the behaviour ---------------------------
def test_reverse_fee_flag_credits_the_fee_back(cfg_reversing):
    """Not a decorative config key: it changes the number, pro-rata."""
    facts = _payment(
        status=PaymentStatus.PARTIALLY_REFUNDED,
        refunds=(RefundFact("rfnd_9001", 40_000, RefundStatus.PROCESSED),),
    )
    result = expected_net_settlement(facts, cfg_reversing)
    # 40% refunded -> 40% of the Rs20.00 fee and Rs3.60 GST come back.
    assert result.fee_reversed == 800
    assert result.tax_reversed == 144
    assert result.expected_net == 57_640 + 944


def test_full_refund_under_reversal_nets_exactly_zero(cfg_reversing):
    """No rounding residue at the boundary: a full refund must net exactly 0."""
    facts = _payment(
        status=PaymentStatus.REFUNDED,
        refunds=(RefundFact("rfnd_9001", 100_000, RefundStatus.PROCESSED),),
    )
    assert expected_net_settlement(facts, cfg_reversing).expected_net == 0


# --- 13. full refund without reversal leaves the merchant negative --------
def test_full_refund_leaves_the_fee_as_a_debit(cfg):
    facts = _payment(
        status=PaymentStatus.REFUNDED,
        refunds=(RefundFact("rfnd_9001", 100_000, RefundStatus.PROCESSED),),
    )
    result = expected_net_settlement(facts, cfg)
    assert result.expected_net == -(2_000 + 360)


# --- 14. adjustments are signed -------------------------------------------
def test_adjustments_apply_with_their_own_sign(cfg):
    debit = _payment(
        adjustments=(AdjustmentFact("adj_1", -50_000, AdjustmentType.CHARGEBACK),)
    )
    credit = _payment(
        adjustments=(
            AdjustmentFact("adj_2", 50_000, AdjustmentType.CHARGEBACK_REVERSAL),
        )
    )
    assert expected_net_settlement(debit, cfg).expected_net == 97_640 - 50_000
    assert expected_net_settlement(credit, cfg).expected_net == 97_640 + 50_000


def test_multiple_adjustments_accumulate(cfg):
    facts = _payment(
        adjustments=(
            AdjustmentFact("adj_1", -50_000, AdjustmentType.CHARGEBACK),
            AdjustmentFact("adj_2", 20_000, AdjustmentType.MANUAL_CREDIT),
        )
    )
    assert expected_net_settlement(facts, cfg).expected_net == 97_640 - 30_000


# --- 15. only processed refunds count -------------------------------------
def test_unprocessed_refunds_are_not_deducted(cfg):
    """A `created` refund has been requested but has moved no money yet."""
    facts = _payment(
        refunds=(
            RefundFact("rfnd_a", 40_000, RefundStatus.CREATED),
            RefundFact("rfnd_b", 10_000, RefundStatus.FAILED),
            RefundFact("rfnd_c", 5_000, RefundStatus.PROCESSED),
        )
    )
    result = expected_net_settlement(facts, cfg)
    assert result.refunded == 5_000
    assert result.expected_net == 97_640 - 5_000


# --- 16. non-settleable statuses contribute nothing -----------------------
def test_failed_payment_expects_nothing(cfg):
    """A failed payment nets exactly zero - it must never become a debit.

    Even with a stray fee row attached, the merchant cannot owe money on a
    payment that never captured.
    """
    result = expected_net_settlement(_payment(status=PaymentStatus.FAILED), cfg)
    assert result.gross == 0
    assert result.fee_charged == 0
    assert result.tax_charged == 0
    assert result.expected_net == 0
    assert DataCondition.NON_SETTLEABLE_STATUS in result.flags


def test_authorized_but_uncaptured_payment_expects_nothing(cfg):
    result = expected_net_settlement(
        _payment(status=PaymentStatus.AUTHORIZED, fee=0, tax=0), cfg
    )
    assert result.gross == 0
    assert result.expected_net == 0


# --- data conditions are surfaced, never absorbed -------------------------
def test_missing_fee_record_is_flagged_not_silently_zeroed(cfg):
    """Test 29: a data bug must not present itself as a financial discrepancy."""
    result = expected_net_settlement(_payment(fee=None, tax=None), cfg)
    assert result.expected_net == 100_000
    assert DataCondition.MISSING_FEE_RECORD in result.flags


def test_zero_fee_is_not_confused_with_a_missing_fee_record(cfg):
    """UPI legitimately has a zero fee; that is not a missing record."""
    result = expected_net_settlement(
        _payment(method=PaymentMethod.UPI, fee=0, tax=0), cfg
    )
    assert result.flags == ()


def test_refund_exceeding_payment_is_flagged(cfg):
    facts = _payment(refunds=(RefundFact("rfnd_x", 150_000, RefundStatus.PROCESSED),))
    result = expected_net_settlement(facts, cfg)
    assert DataCondition.REFUND_EXCEEDS_PAYMENT in result.flags


# --- fee-schedule comparison feeds Phase 6 classification -----------------
def test_fee_schedule_check_reports_the_overcharge(cfg):
    """Rs150 charged where the schedule says Rs20 -> a Rs130 overcharge."""
    facts = _payment(fee=15_000, tax=2_700)
    flags, fee_delta, tax_delta = check_fee_schedule(facts, cfg)
    assert DataCondition.FEE_NOT_PER_SCHEDULE in flags
    assert fee_delta == 2_000 - 15_000  # expected - recorded
    assert tax_delta == 360 - 2_700


def test_fee_schedule_check_is_silent_when_correct(cfg):
    flags, fee_delta, tax_delta = check_fee_schedule(_payment(), cfg)
    assert flags == ()
    assert (fee_delta, tax_delta) == (0, 0)
