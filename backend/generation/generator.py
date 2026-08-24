"""The synthetic world builder.

Produces a financially coherent FreshKart dataset: every settlement line is
derived from the same primitives the reconciliation engine uses, so a clean
dataset reconciles to zero by construction. Phase 4 will corrupt a small
percentage of it deliberately; anything that fails to reconcile *before* that
point is a generator bug, not an exception.

Two rules the builder holds to:

* **Write through the guards.** Currency and refund totals go through
  ``backend.reconciliation.guards``, so the generator cannot create a state the
  database would reject.
* **Nothing settles before its cycle.** A payment captured two days before the
  as-of date has no settlement batch yet - it is legitimately pending, not
  missing. The same applies independently to each refund and adjustment.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable, Sequence

from backend.config import FinancialConfig
from backend.enums import (
    AdjustmentType,
    OrderStatus,
    PaymentMethod,
    PaymentStatus,
    RefundStatus,
    SettlementItemType,
    SettlementStatus,
)
from backend.generation.ids import IdMinter
from backend.generation.profile import MerchantProfile
from backend.reconciliation.fees import compute_fee_and_tax
from backend.reconciliation.guards import validate_currency, validate_refund_total
from backend.reconciliation.settlement_math import (
    adjustment_item_net,
    batch_net,
    payment_item_net,
    refund_item_net,
)
from backend.reconciliation.timing import settlement_eligible_on

BPS = 10_000


@dataclass
class World:
    """A generated dataset, held as insert-ready row dicts."""

    customers: list[dict] = field(default_factory=list)
    orders: list[dict] = field(default_factory=list)
    payments: list[dict] = field(default_factory=list)
    fees: list[dict] = field(default_factory=list)
    refunds: list[dict] = field(default_factory=list)
    adjustments: list[dict] = field(default_factory=list)
    settlements: list[dict] = field(default_factory=list)
    settlement_items: list[dict] = field(default_factory=list)
    truths: list[dict] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def row_count(self) -> int:
        return sum(
            len(x)
            for x in (
                self.customers,
                self.orders,
                self.payments,
                self.fees,
                self.refunds,
                self.adjustments,
                self.settlements,
                self.settlement_items,
                self.truths,
            )
        )


class WorldGenerator:
    def __init__(
        self,
        *,
        seed: int,
        n_payments: int,
        as_of: date,
        cfg: FinancialConfig,
        profile: MerchantProfile | None = None,
    ) -> None:
        self.rng = random.Random(seed)
        self.ids = IdMinter(seed)
        self.n_payments = n_payments
        self.as_of = as_of
        self.cfg = cfg
        self.profile = profile or MerchantProfile()
        self.window_start = as_of - timedelta(days=self.profile.history_days)

    # -- helpers ----------------------------------------------------------

    def _roll(self, rate_bps: int) -> bool:
        return self.rng.randrange(BPS) < rate_bps

    def _weighted(self, weights: dict):
        return self.rng.choices(list(weights), weights=list(weights.values()), k=1)[0]

    def _amount_paise(self) -> int:
        bands = self.profile.amount_bands
        low, high, _ = self.rng.choices(bands, weights=[w for _, _, w in bands], k=1)[0]
        rupees = self.rng.randint(low, high)
        # Most grocery totals carry paise; a minority are round.
        paise = 0 if self._roll(3_000) else self.rng.randrange(100)
        return rupees * 100 + paise

    def _moment(self, day: date) -> datetime:
        """A plausible time of day - grocery orders cluster in daylight hours."""
        hour = self.rng.choices(
            range(6, 24), weights=[2, 4, 6, 7, 7, 6, 8, 9, 7, 6, 6, 7, 9, 10, 9, 7, 5, 3], k=1
        )[0]
        return datetime.combine(
            day, time(hour, self.rng.randrange(60), self.rng.randrange(60)), timezone.utc
        )

    # -- build ------------------------------------------------------------

    def generate(self) -> World:
        world = World()
        customers = self._make_customers(world)

        #: settlement date -> list of settlement-item row dicts
        batches: dict[date, list[dict]] = defaultdict(list)

        for _ in range(self.n_payments):
            self._make_case(world, customers, batches)

        self._make_settlements(world, batches)
        world.stats = self._summarise(world)
        return world

    def _make_customers(self, world: World) -> list[dict]:
        n = max(1, self.n_payments // self.profile.payments_per_customer)
        for _ in range(n):
            world.customers.append(
                {
                    "customer_id": self.ids.mint("cust"),
                    "customer_type": self._weighted(self.profile.customer_type_weights),
                    "created_at": self._moment(
                        self.window_start - timedelta(days=self.rng.randrange(365))
                    ),
                }
            )
        return world.customers

    def _make_case(
        self, world: World, customers: Sequence[dict], batches: dict[date, list[dict]]
    ) -> None:
        customer = self.rng.choice(customers)
        order_day = self.window_start + timedelta(
            days=self.rng.randrange(self.profile.history_days + 1)
        )
        if order_day > self.as_of:
            order_day = self.as_of
        created_at = self._moment(order_day)

        amount = self._amount_paise()
        method = self._weighted(self.profile.method_weights)
        currency = validate_currency("INR")

        order_id = self.ids.mint("order")
        payment_id = self.ids.mint("pay")

        failed = self._roll(self.profile.payment_failure_bps)
        if failed:
            self._append_failed(world, customer, order_id, payment_id, amount, method, created_at, currency)
            return

        captured_at = created_at + timedelta(seconds=self.rng.randrange(5, 240))
        breakdown = compute_fee_and_tax(amount, method, self.cfg)

        # --- refunds -----------------------------------------------------
        refund_rows, refunded_total = self._make_refunds(amount, payment_id, order_id, captured_at)
        validate_refund_total(
            amount, [(r["amount"], r["status"]) for r in refund_rows]
        )

        processed = [r for r in refund_rows if r["status"] == RefundStatus.PROCESSED.value]
        settled_refund_total = sum(r["amount"] for r in processed)
        if settled_refund_total >= amount and settled_refund_total > 0:
            payment_status, order_status = PaymentStatus.REFUNDED, OrderStatus.REFUNDED
        elif settled_refund_total > 0:
            payment_status, order_status = (
                PaymentStatus.PARTIALLY_REFUNDED,
                OrderStatus.PARTIALLY_REFUNDED,
            )
        else:
            payment_status, order_status = PaymentStatus.CAPTURED, OrderStatus.PAID

        world.orders.append(
            {
                "order_id": order_id,
                "customer_id": customer["customer_id"],
                "order_amount": amount,
                "currency": currency,
                "status": order_status.value,
                "created_at": created_at,
            }
        )
        world.payments.append(
            {
                "payment_id": payment_id,
                "order_id": order_id,
                "customer_id": customer["customer_id"],
                "amount": amount,
                "currency": currency,
                "payment_method": method.value,
                "status": payment_status.value,
                "created_at": created_at,
                "captured_at": captured_at,
            }
        )
        world.fees.append(
            {
                "fee_id": self.ids.mint("fee"),
                "payment_id": payment_id,
                "fee_amount": breakdown.fee,
                "tax_amount": breakdown.tax,
                "fee_rate_bps": breakdown.fee_rate_bps,
                "created_at": captured_at,
            }
        )
        world.refunds.extend(refund_rows)
        world.truths.append(
            {
                "payment_id": payment_id,
                "is_exception": False,
                "reason_code": None,
                "explained_amount": None,
                "injection_params": None,
                "notes": None,
            }
        )

        # --- settlement lines --------------------------------------------
        # The payment leg settles on its own cycle...
        pay_day = settlement_eligible_on(captured_at, self.cfg)
        batches[pay_day].append(
            self._item_row(
                SettlementItemType.PAYMENT,
                payment_id=payment_id,
                credit=amount,
                fee=breakdown.fee,
                tax=breakdown.tax,
                net=payment_item_net(amount, breakdown.fee, breakdown.tax),
                created_at=self._moment(pay_day),
            )
        )
        # ...and each refund on its own, which may be a later batch entirely.
        for refund in processed:
            refund_day = settlement_eligible_on(refund["processed_at"], self.cfg)
            batches[refund_day].append(
                self._item_row(
                    SettlementItemType.REFUND,
                    refund_id=refund["refund_id"],
                    net=refund_item_net(refund["amount"]),
                    created_at=self._moment(refund_day),
                )
            )

        # --- adjustments --------------------------------------------------
        if self._roll(self.profile.adjustment_bps):
            self._make_adjustment(world, batches, payment_id, amount, captured_at)

    def _append_failed(
        self, world, customer, order_id, payment_id, amount, method, created_at, currency
    ) -> None:
        """A payment that never captured: no fee, no settlement, nothing owed."""
        world.orders.append(
            {
                "order_id": order_id,
                "customer_id": customer["customer_id"],
                "order_amount": amount,
                "currency": currency,
                "status": OrderStatus.CREATED.value,
                "created_at": created_at,
            }
        )
        world.payments.append(
            {
                "payment_id": payment_id,
                "order_id": order_id,
                "customer_id": customer["customer_id"],
                "amount": amount,
                "currency": currency,
                "payment_method": method.value,
                "status": PaymentStatus.FAILED.value,
                "created_at": created_at,
                "captured_at": None,
            }
        )
        world.truths.append(
            {
                "payment_id": payment_id,
                "is_exception": False,
                "reason_code": None,
                "explained_amount": None,
                "injection_params": None,
                "notes": None,
            }
        )

    def _make_refunds(
        self, amount: int, payment_id: str, order_id: str, captured_at: datetime
    ) -> tuple[list[dict], int]:
        if not self._roll(self.profile.refund_bps):
            return [], 0

        delay = self.rng.randint(*self.profile.refund_delay_days)
        requested_at = captured_at + timedelta(days=delay)
        if requested_at.date() > self.as_of:
            return [], 0  # the customer has not asked yet

        if self._roll(self.profile.full_refund_share_bps):
            refund_amount = amount
        else:
            share = self.rng.randint(
                self.profile.partial_refund_min_bps, self.profile.partial_refund_max_bps
            )
            refund_amount = max(1, amount * share // BPS)

        processed_at = requested_at + timedelta(
            days=self.rng.randint(*self.profile.refund_processing_days)
        )
        # A refund whose processing date has not arrived is still `created`.
        if processed_at.date() > self.as_of:
            status, processed_at = RefundStatus.CREATED, None
        else:
            status = RefundStatus.PROCESSED

        return (
            [
                {
                    "refund_id": self.ids.mint("rfnd"),
                    "payment_id": payment_id,
                    "order_id": order_id,
                    "amount": refund_amount,
                    "status": status.value,
                    "created_at": requested_at,
                    "processed_at": processed_at,
                }
            ],
            refund_amount if status is RefundStatus.PROCESSED else 0,
        )

    def _make_adjustment(
        self,
        world: World,
        batches: dict[date, list[dict]],
        payment_id: str,
        amount: int,
        captured_at: datetime,
    ) -> None:
        adj_type = self._weighted(self.profile.adjustment_type_weights)
        created_at = captured_at + timedelta(days=self.rng.randint(1, 20))
        if created_at.date() > self.as_of:
            return

        if adj_type is AdjustmentType.CHARGEBACK:
            value = -amount
        elif adj_type is AdjustmentType.CHARGEBACK_REVERSAL:
            value = amount
        elif adj_type is AdjustmentType.PLATFORM_FEE:
            value = -self.rng.randrange(500, 5_000)
        elif adj_type is AdjustmentType.MANUAL_CREDIT:
            value = self.rng.randrange(100, 20_000)
        else:
            value = -self.rng.randrange(100, 20_000)

        adjustment_id = self.ids.mint("adj")
        settle_day = settlement_eligible_on(created_at, self.cfg)
        world.adjustments.append(
            {
                "adjustment_id": adjustment_id,
                "settlement_id": None,  # linked once the batch is created
                "payment_id": payment_id,
                "amount": value,
                "type": adj_type.value,
                "reason": f"{adj_type.value} on {payment_id}",
                "created_at": created_at,
            }
        )
        batches[settle_day].append(
            self._item_row(
                SettlementItemType.ADJUSTMENT,
                adjustment_id=adjustment_id,
                net=adjustment_item_net(value),
                created_at=self._moment(settle_day),
            )
        )

    def _item_row(
        self,
        item_type: SettlementItemType,
        *,
        net: int,
        created_at: datetime,
        payment_id: str | None = None,
        refund_id: str | None = None,
        adjustment_id: str | None = None,
        credit: int = 0,
        fee: int = 0,
        tax: int = 0,
    ) -> dict:
        return {
            "item_id": self.ids.mint("si"),
            "settlement_id": None,  # assigned when the batch is created
            "item_type": item_type.value,
            "payment_id": payment_id,
            "refund_id": refund_id,
            "adjustment_id": adjustment_id,
            "credit_amount": credit,
            "debit_fee": fee,
            "debit_tax": tax,
            "net_amount": net,
            "created_at": created_at,
        }

    def _make_settlements(self, world: World, batches: dict[date, list[dict]]) -> None:
        """One payout per settlement date - but only for dates that have arrived.

        Lines dated after the as-of date are simply not emitted: that money is
        legitimately still in flight, and inventing a batch for it would make
        the dataset claim settlements that have not happened.
        """
        adjustment_batch: dict[str, str] = {}
        pending_lines = 0

        for settle_day in sorted(batches):
            lines = batches[settle_day]
            if settle_day > self.as_of:
                pending_lines += len(lines)
                continue

            settlement_id = self.ids.mint("setl")
            settled_at = self._moment(settle_day)
            for line in lines:
                line["settlement_id"] = settlement_id
                if line["adjustment_id"]:
                    adjustment_batch[line["adjustment_id"]] = settlement_id
            world.settlements.append(
                {
                    "settlement_id": settlement_id,
                    "net_amount": batch_net(line["net_amount"] for line in lines),
                    "utr": f"UTR{self.rng.randrange(10**11, 10**12)}",
                    "status": SettlementStatus.PROCESSED.value,
                    "settlement_date": settled_at,
                    "created_at": settled_at,
                }
            )
            world.settlement_items.extend(lines)

        for adjustment in world.adjustments:
            adjustment["settlement_id"] = adjustment_batch.get(adjustment["adjustment_id"])

        world.stats["lines_not_yet_settled"] = pending_lines

    # -- reporting --------------------------------------------------------

    def _summarise(self, world: World) -> dict:
        stats = dict(world.stats)
        settled_payment_ids = {
            i["payment_id"]
            for i in world.settlement_items
            if i["item_type"] == SettlementItemType.PAYMENT.value
        }
        gross = sum(
            p["amount"]
            for p in world.payments
            if p["status"] != PaymentStatus.FAILED.value
        )
        stats.update(
            {
                "customers": len(world.customers),
                "orders": len(world.orders),
                "payments": len(world.payments),
                "fees": len(world.fees),
                "refunds": len(world.refunds),
                "adjustments": len(world.adjustments),
                "settlements": len(world.settlements),
                "settlement_items": len(world.settlement_items),
                "failed_payments": sum(
                    1 for p in world.payments if p["status"] == PaymentStatus.FAILED.value
                ),
                "settled_payments": len(settled_payment_ids),
                "pending_payments": sum(
                    1
                    for p in world.payments
                    if p["status"] != PaymentStatus.FAILED.value
                    and p["payment_id"] not in settled_payment_ids
                ),
                "gross_paise": gross,
                "fee_paise": sum(f["fee_amount"] for f in world.fees),
                "tax_paise": sum(f["tax_amount"] for f in world.fees),
                "refund_paise": sum(
                    r["amount"]
                    for r in world.refunds
                    if r["status"] == RefundStatus.PROCESSED.value
                ),
                "adjustment_paise": sum(a["amount"] for a in world.adjustments),
                "settled_net_paise": sum(s["net_amount"] for s in world.settlements),
                "method_mix": _counts(p["payment_method"] for p in world.payments),
                "status_mix": _counts(p["status"] for p in world.payments),
            }
        )
        return stats


def _counts(values: Iterable[str]) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for v in values:
        out[v] += 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def generate_world(
    *,
    seed: int,
    n_payments: int,
    as_of: date,
    cfg: FinancialConfig,
    profile: MerchantProfile | None = None,
) -> World:
    return WorldGenerator(
        seed=seed, n_payments=n_payments, as_of=as_of, cfg=cfg, profile=profile
    ).generate()
