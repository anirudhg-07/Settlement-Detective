"""G4 - sign convention and the conservation invariants.

These are the tests that discharge the Phase 1 obligation to verify the
financial model is internally consistent *before* the generator is built.

The property tests compare two independent paths to the same number: the
expectation calculator on one side, the settlement-item constructors on the
other. A sign error anywhere makes them disagree.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

import pytest

from backend.config import FinancialConfig
from backend.enums import PaymentMethod
from backend.reconciliation.settlement_math import (
    adjustment_item_net,
    batch_net,
    expected_net_settlement,
    payment_item_net,
    reconcile_batch,
    refund_item_net,
)
from tests.factories import build_case

# Rs0.01 to Rs1,00,000.00
AMOUNTS = st.integers(min_value=1, max_value=10_000_000)
METHODS = st.sampled_from(list(PaymentMethod))
ADJUSTMENTS = st.lists(
    st.integers(min_value=-500_000, max_value=500_000), max_size=3
).map(tuple)


@st.composite
def case_inputs(draw):
    """A payment plus refunds that never exceed it."""
    amount = draw(AMOUNTS)
    method = draw(METHODS)
    n_refunds = draw(st.integers(min_value=0, max_value=3))
    remaining = amount
    refunds = []
    for _ in range(n_refunds):
        if remaining <= 0:
            break
        r = draw(st.integers(min_value=1, max_value=remaining))
        refunds.append(r)
        remaining -= r
    return amount, method, tuple(refunds), draw(ADJUSTMENTS)


# --- 17 & 18. the sign convention ----------------------------------------
def test_payment_line_net_is_credit_less_deductions():
    assert payment_item_net(100_000, 2_000, 360) == 97_640


def test_refund_line_is_always_a_debit():
    assert refund_item_net(40_000) == -40_000
    assert refund_item_net(0) == 0


def test_refund_amounts_are_stored_unsigned():
    """The sign is applied in exactly one place; passing it in twice is a bug."""
    with pytest.raises(ValueError, match="unsigned"):
        refund_item_net(-40_000)


def test_adjustment_line_carries_its_own_sign():
    assert adjustment_item_net(-50_000) == -50_000
    assert adjustment_item_net(50_000) == 50_000


# --- 19. batch invariant: sum of lines IS the payout, exactly -------------
@settings(max_examples=300, deadline=None)
@given(case_inputs())
def test_batch_net_is_the_exact_sum_of_its_lines(inputs):
    cfg = FinancialConfig()
    amount, method, refunds, adjustments = inputs
    case = build_case("pay_x", amount, method, refunds, adjustments, cfg)
    settlement_net = batch_net(case.item_nets)
    # No tolerance: a batch is an arithmetic identity, not a comparison.
    assert reconcile_batch(settlement_net, case.item_nets) == 0


# --- THE consistency proof: two independent paths agree -------------------
@settings(max_examples=500, deadline=None)
@given(case_inputs())
def test_expected_settlement_equals_the_lines_it_should_produce(inputs):
    """The calculator and the item constructors must never disagree.

    If this fails, the financial model is not internally consistent and the
    synthetic generator must not be built on top of it.
    """
    cfg = FinancialConfig()
    amount, method, refunds, adjustments = inputs
    case = build_case("pay_x", amount, method, refunds, adjustments, cfg)
    expected = expected_net_settlement(case.facts, cfg)
    assert expected.expected_net == batch_net(case.item_nets)


@settings(max_examples=300, deadline=None)
@given(case_inputs())
def test_consistency_holds_under_fee_reversal_too(inputs):
    """The same proof with `reverse_fee_on_refund = True`."""
    cfg = FinancialConfig(reverse_fee_on_refund=True)
    amount, method, refunds, adjustments = inputs
    case = build_case("pay_x", amount, method, refunds, adjustments, cfg)
    expected = expected_net_settlement(case.facts, cfg)
    assert expected.expected_net == batch_net(case.item_nets)


@settings(max_examples=300, deadline=None)
@given(case_inputs())
def test_components_sum_to_expected_net(inputs):
    """Every evidence line must add up to the number it claims to explain."""
    cfg = FinancialConfig()
    amount, method, refunds, adjustments = inputs
    case = build_case("pay_x", amount, method, refunds, adjustments, cfg)
    expected = expected_net_settlement(case.facts, cfg)
    assert sum(expected.components().values()) == expected.expected_net


# --- 20. global conservation across a whole dataset -----------------------
@settings(max_examples=40, deadline=None)
@given(st.lists(case_inputs(), min_size=1, max_size=25))
def test_global_conservation_across_many_cases(all_inputs):
    """Sum of every settlement line equals the sum of the model's components.

    A sign error that happens to cancel within one payment cannot survive this.
    """
    cfg = FinancialConfig()
    total_lines = 0
    total_gross = total_fee = total_tax = total_refunded = total_adjusted = 0

    for i, (amount, method, refunds, adjustments) in enumerate(all_inputs):
        case = build_case(f"pay_{i}", amount, method, refunds, adjustments, cfg)
        expected = expected_net_settlement(case.facts, cfg)
        total_lines += batch_net(case.item_nets)
        total_gross += expected.gross
        total_fee += expected.fee_retained
        total_tax += expected.tax_retained
        total_refunded += expected.refunded
        total_adjusted += expected.adjusted

    assert total_lines == (
        total_gross - total_fee - total_tax - total_refunded + total_adjusted
    )
