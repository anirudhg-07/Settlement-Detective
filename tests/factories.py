"""Minimal consistent-world builder used by the property tests.

Builds a payment together with the settlement lines it *should* produce, so the
invariant tests compare two independent paths to the same number: the
expectation calculator on one side, the settlement-item constructors on the
other. If those two ever disagree, the financial model is not internally
consistent and Phase 3 must not be built on it.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.config import FinancialConfig
from backend.enums import (
    AdjustmentType,
    PaymentMethod,
    PaymentStatus,
    RefundStatus,
)
from backend.reconciliation.fees import compute_fee_and_tax
from backend.reconciliation.settlement_math import (
    AdjustmentFact,
    PaymentFacts,
    RefundFact,
    adjustment_item_net,
    payment_item_net,
    refund_item_net,
)


@dataclass(frozen=True)
class BuiltCase:
    facts: PaymentFacts
    item_nets: tuple[int, ...]
    fee: int
    tax: int


def build_case(
    payment_id: str,
    amount: int,
    method: PaymentMethod,
    refund_amounts: tuple[int, ...],
    adjustment_amounts: tuple[int, ...],
    cfg: FinancialConfig,
) -> BuiltCase:
    """A payment and the settlement lines a correct gateway would emit for it."""
    breakdown = compute_fee_and_tax(amount, method, cfg)
    fee, tax = breakdown.fee, breakdown.tax

    refunds = tuple(
        RefundFact(f"{payment_id}_rfnd_{i}", amt, RefundStatus.PROCESSED)
        for i, amt in enumerate(refund_amounts)
    )
    adjustments = tuple(
        AdjustmentFact(f"{payment_id}_adj_{i}", amt, AdjustmentType.MANUAL_CREDIT)
        for i, amt in enumerate(adjustment_amounts)
    )
    refunded = sum(refund_amounts)

    if refunded == 0:
        status = PaymentStatus.CAPTURED
    elif refunded >= amount:
        status = PaymentStatus.REFUNDED
    else:
        status = PaymentStatus.PARTIALLY_REFUNDED

    facts = PaymentFacts(
        payment_id=payment_id,
        amount=amount,
        method=method,
        status=status,
        fee=fee,
        tax=tax,
        refunds=refunds,
        adjustments=adjustments,
    )

    nets = [payment_item_net(amount, fee, tax)]
    nets.extend(refund_item_net(amt) for amt in refund_amounts)
    nets.extend(adjustment_item_net(amt) for amt in adjustment_amounts)

    if cfg.reverse_fee_on_refund and refunded > 0:
        # A gateway that reverses fees emits a credit line for the reversal.
        from backend.reconciliation.settlement_math import _reversal

        fee_rev, tax_rev = _reversal(amount, refunded, fee, tax, cfg)
        nets.append(adjustment_item_net(fee_rev + tax_rev))

    return BuiltCase(facts=facts, item_nets=tuple(nets), fee=fee, tax=tax)
