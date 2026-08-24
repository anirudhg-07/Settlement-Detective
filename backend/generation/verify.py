"""Reconcile a generated ``World`` in memory.

This is the generator's own proof of correctness, not the Phase 5 engine: it
touches no database and persists nothing. A clean world must reconcile to a
100% match rate. Anything that does not is a generator bug, and finding it here
rather than after Phase 4 injects deliberate faults is the whole point - once
faults are mixed in, a generator bug is indistinguishable from an exception.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from backend.config import FinancialConfig
from backend.enums import (
    PaymentMethod,
    PaymentStatus,
    ReconStatus,
    RefundStatus,
    SettlementStatus,
)
from backend.generation.generator import World
from backend.reconciliation.settlement_math import (
    AdjustmentFact,
    PaymentFacts,
    RefundFact,
    ReconOutcome,
    expected_net_settlement,
    reconcile_payment,
)


def reconcile_world(world: World, cfg: FinancialConfig, as_of: date) -> dict:
    """Run every payment in the world through the reconciliation decision."""
    fees = {f["payment_id"]: f for f in world.fees}
    refunds_by_payment: dict[str, list[dict]] = defaultdict(list)
    for r in world.refunds:
        refunds_by_payment[r["payment_id"]].append(r)
    adjustments_by_payment: dict[str, list[dict]] = defaultdict(list)
    for a in world.adjustments:
        if a["payment_id"]:
            adjustments_by_payment[a["payment_id"]].append(a)

    refund_to_payment = {r["refund_id"]: r["payment_id"] for r in world.refunds}
    adjustment_to_payment = {
        a["adjustment_id"]: a["payment_id"] for a in world.adjustments
    }

    # ACTUAL: every line that resolves to a payment, directly or via the refund
    # or adjustment it belongs to - but only from batches that have actually
    # been paid out. A line sitting in a `created` batch is money scheduled,
    # not money received, and counting it would make a partial settlement look
    # complete.
    processed_batches = {
        s["settlement_id"]
        for s in world.settlements
        if s["status"] == SettlementStatus.PROCESSED.value
    }
    actual: dict[str, int] = defaultdict(int)
    has_payment_line: set[str] = set()
    for item in world.settlement_items:
        if item["settlement_id"] not in processed_batches:
            continue
        if item["payment_id"]:
            pid = item["payment_id"]
            has_payment_line.add(pid)
        elif item["refund_id"]:
            pid = refund_to_payment.get(item["refund_id"])
        else:
            pid = adjustment_to_payment.get(item["adjustment_id"])
        if pid:
            actual[pid] += item["net_amount"]

    counts: dict[str, int] = defaultdict(int)
    mismatches: list[ReconOutcome] = []

    for payment in world.payments:
        pid = payment["payment_id"]
        fee = fees.get(pid)
        facts = PaymentFacts(
            payment_id=pid,
            amount=payment["amount"],
            method=PaymentMethod(payment["payment_method"]),
            status=PaymentStatus(payment["status"]),
            captured_at=payment["captured_at"],
            fee=fee["fee_amount"] if fee else None,
            tax=fee["tax_amount"] if fee else None,
            refunds=tuple(
                RefundFact(
                    r["refund_id"],
                    r["amount"],
                    RefundStatus(r["status"]),
                    processed_at=r["processed_at"],
                )
                for r in refunds_by_payment[pid]
            ),
            adjustments=tuple(
                AdjustmentFact(
                    a["adjustment_id"], a["amount"], a["type"], created_at=a["created_at"]
                )
                for a in adjustments_by_payment[pid]
            ),
        )
        expected = expected_net_settlement(facts, cfg, as_of=as_of)
        outcome = reconcile_payment(
            expected,
            actual_net=actual.get(pid, 0),
            has_settled_items=pid in has_payment_line,
            captured_at=payment["captured_at"],
            as_of=as_of,
            cfg=cfg,
        )
        counts[outcome.status.value] += 1
        if outcome.is_exception:
            mismatches.append(outcome)

    total = len(world.payments)
    reconciled = counts.get(ReconStatus.MATCHED.value, 0) + counts.get(
        ReconStatus.PENDING_SETTLEMENT.value, 0
    )
    return {
        "total": total,
        "counts": dict(counts),
        "match_rate_bps": (reconciled * 10_000 // total) if total else 0,
        "mismatches": mismatches,
    }


def batch_residuals(world: World) -> list[tuple[str, int]]:
    """Batches whose payout does not equal the sum of their lines. Must be empty."""
    sums: dict[str, int] = defaultdict(int)
    for item in world.settlement_items:
        sums[item["settlement_id"]] += item["net_amount"]
    return [
        (s["settlement_id"], s["net_amount"] - sums[s["settlement_id"]])
        for s in world.settlements
        if s["net_amount"] != sums[s["settlement_id"]]
    ]
