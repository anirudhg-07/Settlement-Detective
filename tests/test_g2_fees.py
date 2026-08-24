"""G2 - fee and GST calculation."""

from __future__ import annotations

from decimal import Decimal
from types import MappingProxyType

import pytest

from backend.config import FinancialConfig
from backend.enums import PaymentMethod
from backend.money import apply_bps
from backend.reconciliation.fees import compute_fee, compute_fee_and_tax, compute_tax


# --- 5. the worked Rs1,000 example from the Phase 1 spec, asserted literally ---
def test_worked_thousand_rupee_example(cfg):
    """Part C, Case 1: Rs1,000 card payment nets Rs976.40."""
    amount = 100_000  # Rs1,000.00
    breakdown = compute_fee_and_tax(amount, PaymentMethod.CARD, cfg)
    assert breakdown.fee == 2_000  # Rs20.00
    assert breakdown.tax == 360  # Rs3.60
    assert amount - breakdown.total_deduction == 97_640  # Rs976.40


# --- 6. rounding happens half-up at the paise boundary --------------------
def test_fee_rounds_half_up_at_paise_boundary():
    """A fee of exactly 1.5 paise must become 2, not 1."""
    cfg = FinancialConfig(
        fee_schedule_bps=MappingProxyType({m: 1 for m in PaymentMethod})
    )
    # 1 bp (0.01%) of 15,000 paise = exactly 1.5 paise
    assert compute_fee(15_000, PaymentMethod.CARD, cfg) == 2
    assert compute_fee(5_000, PaymentMethod.CARD, cfg) == 1  # 0.5 paise -> 1
    assert compute_fee(4_900, PaymentMethod.CARD, cfg) == 0  # 0.49 paise -> 0
    assert compute_fee(2_500, PaymentMethod.CARD, cfg) == 0  # 0.25 paise -> 0


# --- 7. tax is on the fee, never on the payment ---------------------------
def test_tax_is_computed_on_fee_not_on_gross(cfg):
    """The single easiest mistake in this model: it inflates GST ~50x at 2%."""
    amount = 100_000
    breakdown = compute_fee_and_tax(amount, PaymentMethod.CARD, cfg)
    tax_on_gross = apply_bps(amount, cfg.gst_rate_bps)
    assert breakdown.tax == 360
    assert tax_on_gross == 18_000
    assert breakdown.tax != tax_on_gross


def test_compute_tax_matches_breakdown(cfg):
    fee = compute_fee(250_000, PaymentMethod.NETBANKING, cfg)
    assert compute_tax(fee, cfg) == compute_fee_and_tax(
        250_000, PaymentMethod.NETBANKING, cfg
    ).tax


# --- 8. the schedule is per method; a zero-rate method yields zero --------
def test_zero_rate_method_yields_no_fee_and_no_tax(cfg):
    breakdown = compute_fee_and_tax(100_000, PaymentMethod.UPI, cfg)
    assert breakdown.fee == 0
    assert breakdown.tax == 0
    assert breakdown.fee_rate_bps == 0


def test_each_method_uses_its_own_rate(cfg):
    amount = 100_000
    assert compute_fee(amount, PaymentMethod.CARD, cfg) == 2_000  # 2.00%
    assert compute_fee(amount, PaymentMethod.NETBANKING, cfg) == 1_900  # 1.90%
    assert compute_fee(amount, PaymentMethod.WALLET, cfg) == 2_000  # 2.00%
    assert compute_fee(amount, PaymentMethod.UPI, cfg) == 0  # 0.00%


def test_unknown_method_raises(cfg):
    with pytest.raises(ValueError, match="no fee rate configured"):
        compute_fee(100_000, "crypto", cfg)


# --- 9. awkward amounts, table-driven -------------------------------------
@pytest.mark.parametrize(
    "amount,expected_fee,expected_tax",
    [
        (1, 0, 0),  # Rs0.01  -> 0.02 paise fee -> 0
        (100, 2, 0),  # Rs1.00  -> 2 paise fee -> 0.36 paise tax -> 0
        (99_999, 2_000, 360),  # Rs999.99
        (10_000_000, 200_000, 36_000),  # Rs1,00,000.00
        (99_999_999, 2_000_000, 360_000),  # Rs9,99,999.99
        (25, 1, 0),  # Rs0.25 -> 0.5 paise -> rounds up to 1
    ],
)
def test_awkward_amounts(amount, expected_fee, expected_tax, cfg):
    breakdown = compute_fee_and_tax(amount, PaymentMethod.CARD, cfg)
    assert (breakdown.fee, breakdown.tax) == (expected_fee, expected_tax)


def test_apply_bps_uses_exact_decimal_arithmetic():
    """0.1 + 0.2 problems must not exist here."""
    total = sum(apply_bps(10, 1000) for _ in range(10))  # 10% of 10 paise, ten times
    assert total == 10
    assert apply_bps(10, 1000) == 1
    assert Decimal(apply_bps(333, 1800)) == Decimal(60)  # 59.94 -> 60
