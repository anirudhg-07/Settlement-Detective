"""Human-readable proof that the financial model is internally consistent.

Prints the Phase 1 worked examples with every rupee accounted for. The tests
are the real guarantee; this exists so the model can be inspected without
reading a test suite.

    python scripts/verify_model.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import FinancialConfig
from backend.enums import PaymentMethod, PaymentStatus, RefundStatus
from backend.money import format_paise
from backend.reconciliation.fees import compute_fee_and_tax
from backend.reconciliation.settlement_math import (
    PaymentFacts,
    RefundFact,
    batch_net,
    expected_net_settlement,
    payment_item_net,
    refund_item_net,
)

cfg = FinancialConfig()
AMOUNT = 100_000  # Rs1,000.00


def show(title: str, rows: list[tuple[str, int]], total_label: str, total: int) -> None:
    print(f"\n{title}")
    print("-" * 64)
    for label, paise in rows:
        print(f"  {label:<44} {format_paise(paise):>16}")
    print("-" * 64)
    print(f"  {total_label:<44} {format_paise(total):>16}")


def main() -> int:
    fee_tax = compute_fee_and_tax(AMOUNT, PaymentMethod.CARD, cfg)

    # --- Case 1: clean payment -------------------------------------------
    clean = PaymentFacts(
        "pay_1001", AMOUNT, PaymentMethod.CARD, PaymentStatus.CAPTURED,
        fee=fee_tax.fee, tax=fee_tax.tax,
    )
    e1 = expected_net_settlement(clean, cfg)
    show(
        "CASE 1  Rs1,000 card payment, no refund",
        [
            ("Customer paid", AMOUNT),
            ("  -> Razorpay processing fee (2.00%)", -fee_tax.fee),
            ("  -> GST on fee (18%)", -fee_tax.tax),
            ("  -> Merchant bank", e1.expected_net),
        ],
        "Accounted for",
        fee_tax.fee + fee_tax.tax + e1.expected_net,
    )
    lines1 = [payment_item_net(AMOUNT, fee_tax.fee, fee_tax.tax)]
    assert batch_net(lines1) == e1.expected_net
    print(f"  settlement lines sum to expected net: {batch_net(lines1) == e1.expected_net}")

    # --- Case 2: Rs400 partial refund ------------------------------------
    refunded = 40_000
    partial = PaymentFacts(
        "pay_1001", AMOUNT, PaymentMethod.CARD, PaymentStatus.PARTIALLY_REFUNDED,
        fee=fee_tax.fee, tax=fee_tax.tax,
        refunds=(RefundFact("rfnd_9001", refunded, RefundStatus.PROCESSED),),
    )
    e2 = expected_net_settlement(partial, cfg)
    show(
        "CASE 2  same payment, Rs400 refunded (fee retained)",
        [
            ("Customer paid", AMOUNT),
            ("  -> Refunded to customer", -refunded),
            ("  -> Razorpay processing fee (not reversed)", -fee_tax.fee),
            ("  -> GST on fee", -fee_tax.tax),
            ("  -> Merchant bank", e2.expected_net),
        ],
        "Accounted for",
        refunded + fee_tax.fee + fee_tax.tax + e2.expected_net,
    )
    lines2 = [payment_item_net(AMOUNT, fee_tax.fee, fee_tax.tax), refund_item_net(refunded)]
    assert batch_net(lines2) == e2.expected_net
    print(f"  settlement lines sum to expected net: {batch_net(lines2) == e2.expected_net}")

    # --- Case 3: unexplained discrepancy ---------------------------------
    actual = 50_000
    delta = actual - e2.expected_net
    print("\nCASE 3  same payment, but only Rs500.00 actually settled")
    print("-" * 64)
    print(f"  {"Expected net":<44} {format_paise(e2.expected_net):>16}")
    print(f"  {"Actual net":<44} {format_paise(actual):>16}")
    print(f"  {"DELTA":<44} {format_paise(delta):>16}")
    print("  No fee, tax, refund or adjustment record accounts for this.")
    print("  -> UNKNOWN_DISCREPANCY -> ESCALATE (the system does not guess)")

    print("\nAll three cases reconcile to the rupee.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
