"""Deliberate exception injection.

Phase 3 produced a world that reconciles at 100%. This module breaks a small,
controlled percentage of it in ways a real payment platform actually breaks,
and records in ``gt.case_truth`` exactly what was done and by how much.

Three rules every injector holds to:

1. **Batches stay balanced.** Whenever a line's net changes, or a line is added
   or removed, the batch payout moves with it. A settlement whose lines no
   longer sum to its payout is an accounting artifact, not an exception, and it
   would let the reconciler "detect" faults that were never injected.

2. **The fault must be the reason.** A corrupted payment must fail
   reconciliation for the cause ground truth claims - not incidentally.

3. **Ground truth never reaches the agent.** These records go to schema ``gt``,
   which the ``sd_agent`` role has no grant on.

Not every exception is visible as a non-zero delta. ``DUPLICATE_PAYMENT`` and
``UNEXPECTED_ADJUSTMENT`` reconcile perfectly at the payment level - the money
adds up, but it should never have moved. Those are found by rule in Phase 6,
and each injector declares which kind it is via ``delta_visible``.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable

from backend.config import FinancialConfig
from backend.enums import (
    AdjustmentType,
    ExceptionType,
    PaymentStatus,
    RefundStatus,
    SettlementItemType,
    SettlementStatus,
)
from backend.generation.generator import World
from backend.generation.ids import IdMinter
from backend.reconciliation.fees import compute_fee_and_tax
from backend.reconciliation.settlement_math import payment_item_net, refund_item_net
from backend.reconciliation.timing import settlement_eligible_on

BPS = 10_000

#: Difficulty families that exist to separate rule-based investigation from
#: genuine multi-record reasoning. These are the cases a single-hypothesis
#: matcher cannot close, and they are where the AI has to earn its place.
FAMILY_MULTI_CAUSE = "MULTI_CAUSE"
FAMILY_CROSS_ENTITY = "CROSS_ENTITY"
FAMILY_TIMING_SHIFTED = "TIMING_SHIFTED"


@dataclass
class Injection:
    """What an injector did, for the ground-truth record."""

    reason_code: str
    #: Signed paise the cause accounts for. ``None`` means deliberately
    #: unexplainable - the agent must not be able to close it.
    explained_amount: int | None
    params: dict
    notes: str
    delta_visible: bool = True
    #: Whether the payment the injector was handed is itself the faulty record.
    #: A duplicate charge creates a *second* payment; the original is
    #: blameless, and flagging it would inflate every exception count.
    mark_source: bool = True


@dataclass
class InjectionReport:
    injected: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    attempted: int = 0
    skipped: int = 0

    def total(self) -> int:
        return sum(self.injected.values())


class ExceptionInjector:
    """Mutates a generated ``World`` in place."""

    def __init__(
        self,
        world: World,
        *,
        cfg: FinancialConfig,
        as_of: date,
        seed: int,
    ) -> None:
        self.world = world
        self.cfg = cfg
        self.as_of = as_of
        self.rng = random.Random(seed ^ 0x5EED)
        self.ids = IdMinter(seed ^ 0xC0FFEE)

        self.payments = {p["payment_id"]: p for p in world.payments}
        self.fees = {f["payment_id"]: f for f in world.fees}
        self.settlements = {s["settlement_id"]: s for s in world.settlements}
        self.truths = {t["payment_id"]: t for t in world.truths}

        self.refunds_by_payment: dict[str, list[dict]] = defaultdict(list)
        for r in world.refunds:
            self.refunds_by_payment[r["payment_id"]].append(r)

        self.payment_line: dict[str, dict] = {}
        self.refund_line: dict[str, dict] = {}
        for item in world.settlement_items:
            if item["item_type"] == SettlementItemType.PAYMENT.value:
                self.payment_line[item["payment_id"]] = item
            elif item["item_type"] == SettlementItemType.REFUND.value:
                self.refund_line[item["refund_id"]] = item

        self.claimed: set[str] = set()

    # -- batch bookkeeping ------------------------------------------------

    def _shift_batch(self, settlement_id: str, delta: int) -> None:
        """Move a batch payout by ``delta`` so its lines still sum to it."""
        self.settlements[settlement_id]["net_amount"] += delta

    def _retune_line(self, line: dict, new_net: int) -> None:
        self._shift_batch(line["settlement_id"], new_net - line["net_amount"])
        line["net_amount"] = new_net

    def _future_batch(self, days_ahead: int = 3) -> dict:
        """An unprocessed batch dated after the as-of date.

        Money scheduled but not yet paid out. Used by partial settlement and
        the timing-shifted family - both hinge on a debit that exists but has
        not landed.
        """
        settle_on = self.as_of + timedelta(days=days_ahead)
        settlement = {
            "settlement_id": self.ids.mint("setl"),
            "net_amount": 0,
            "utr": None,
            "status": SettlementStatus.CREATED.value,
            "settlement_date": _at_noon(settle_on),
            "created_at": _at_noon(self.as_of),
        }
        self.world.settlements.append(settlement)
        self.settlements[settlement["settlement_id"]] = settlement
        return settlement

    # -- candidate selection ----------------------------------------------

    def _candidates(self, *, needs_refund: bool = False) -> list[str]:
        out = []
        for pid, payment in self.payments.items():
            if pid in self.claimed:
                continue
            if payment["status"] == PaymentStatus.FAILED.value:
                continue
            if pid not in self.payment_line:
                continue  # not settled yet - legitimately pending
            if needs_refund and not [
                r
                for r in self.refunds_by_payment[pid]
                if r["status"] == RefundStatus.PROCESSED.value
                and r["refund_id"] in self.refund_line
            ]:
                continue
            out.append(pid)
        return out

    def _settled_refund(self, payment_id: str) -> tuple[dict, dict]:
        refund = self.rng.choice(
            [
                r
                for r in self.refunds_by_payment[payment_id]
                if r["status"] == RefundStatus.PROCESSED.value
                and r["refund_id"] in self.refund_line
            ]
        )
        return refund, self.refund_line[refund["refund_id"]]

    # ==================================================================
    # Injectors
    # ==================================================================

    def missing_settlement(self, pid: str) -> Injection:
        """The payment captured, the cycle elapsed, the money never arrived."""
        line = self.payment_line.pop(pid)
        self._shift_batch(line["settlement_id"], -line["net_amount"])
        self.world.settlement_items.remove(line)
        return Injection(
            reason_code=ExceptionType.MISSING_SETTLEMENT.value,
            explained_amount=-line["net_amount"],
            params={"removed_item": line["item_id"], "batch": line["settlement_id"]},
            notes="settlement line removed; payment captured and past its cycle",
        )

    def duplicate_payment(self, pid: str) -> Injection:
        """The customer was charged twice for one order.

        Both payments reconcile perfectly on their own - the money adds up.
        The fault is that the second one should never have existed, which is
        why this is found by rule and not by a delta.
        """
        original = self.payments[pid]
        twin_id = self.ids.mint("pay")
        captured_at = original["captured_at"] + timedelta(seconds=self.rng.randrange(30, 900))
        breakdown = compute_fee_and_tax(
            original["amount"], original["payment_method"], self.cfg
        )
        twin = dict(
            original,
            payment_id=twin_id,
            status=PaymentStatus.CAPTURED.value,
            created_at=original["created_at"],
            captured_at=captured_at,
        )
        self.world.payments.append(twin)
        self.payments[twin_id] = twin
        self.world.fees.append(
            {
                "fee_id": self.ids.mint("fee"),
                "payment_id": twin_id,
                "fee_amount": breakdown.fee,
                "tax_amount": breakdown.tax,
                "fee_rate_bps": breakdown.fee_rate_bps,
                "created_at": captured_at,
            }
        )
        settle_day = settlement_eligible_on(captured_at, self.cfg)
        settlement = self._batch_on(settle_day)
        line = {
            "item_id": self.ids.mint("si"),
            "settlement_id": settlement["settlement_id"],
            "item_type": SettlementItemType.PAYMENT.value,
            "payment_id": twin_id,
            "refund_id": None,
            "adjustment_id": None,
            "credit_amount": original["amount"],
            "debit_fee": breakdown.fee,
            "debit_tax": breakdown.tax,
            "net_amount": payment_item_net(
                original["amount"], breakdown.fee, breakdown.tax
            ),
            "created_at": _at_noon(settle_day),
        }
        self.world.settlement_items.append(line)
        self.payment_line[twin_id] = line
        self._shift_batch(settlement["settlement_id"], line["net_amount"])

        self.world.truths.append(
            {
                "payment_id": twin_id,
                "is_exception": True,
                "reason_code": ExceptionType.DUPLICATE_PAYMENT.value,
                "explained_amount": original["amount"],
                "injection_params": {"duplicate_of": pid},
                "notes": "second capture for the same order",
            }
        )
        self.truths[twin_id] = self.world.truths[-1]
        self.claimed.add(twin_id)
        return Injection(
            reason_code=ExceptionType.DUPLICATE_PAYMENT.value,
            explained_amount=original["amount"],
            params={"duplicate_payment": twin_id, "order_id": original["order_id"]},
            notes="the same order was charged twice",
            delta_visible=False,
            mark_source=False,
        )

    def missing_refund(self, pid: str) -> Injection:
        """A refund is recorded and due, but was never debited from a payout.

        The merchant is still holding money they have already returned to the
        customer on paper.

        NOTE: the mirror-image variant - a settlement debit with no refund
        record behind it - is unrepresentable here, and deliberately so: the
        foreign key from `settlement_items.refund_id` makes an orphan debit
        impossible to create. Referential integrity is the right guarantee to
        keep, so this is the variant that is modelled.
        """
        refund, line = self._settled_refund(pid)
        self._shift_batch(line["settlement_id"], -line["net_amount"])
        self.world.settlement_items.remove(line)
        del self.refund_line[refund["refund_id"]]
        return Injection(
            reason_code=ExceptionType.MISSING_REFUND.value,
            explained_amount=refund["amount"],
            params={"refund_id": refund["refund_id"], "amount": refund["amount"]},
            notes="refund processed and due, but never debited from any settlement",
        )

    def incorrect_refund_amount(self, pid: str) -> Injection:
        """The settlement debited a different amount than the refund record."""
        refund, line = self._settled_refund(pid)
        drift = max(100, refund["amount"] * self.rng.randint(500, 4_000) // BPS)
        if self.rng.random() < 0.5:
            drift = -drift
        debited = max(1, refund["amount"] + drift)
        self._retune_line(line, refund_item_net(debited))
        return Injection(
            reason_code=ExceptionType.INCORRECT_REFUND_AMOUNT.value,
            explained_amount=refund["amount"] - debited,
            params={
                "refund_id": refund["refund_id"],
                "recorded": refund["amount"],
                "debited": debited,
            },
            notes=f"refund record says {refund['amount']} paise, settlement debited {debited}",
        )

    def fee_mismatch(self, pid: str) -> Injection:
        """The settlement deducted more fee than the fee record states."""
        line = self.payment_line[pid]
        fee = self.fees[pid]
        surcharge = max(
            100, fee["fee_amount"] * self.rng.randint(2_500, 15_000) // BPS
        )
        line["debit_fee"] += surcharge
        self._retune_line(line, line["net_amount"] - surcharge)
        return Injection(
            reason_code=ExceptionType.FEE_MISMATCH.value,
            explained_amount=-surcharge,
            params={
                "recorded_fee": fee["fee_amount"],
                "deducted_fee": line["debit_fee"],
                "surcharge": surcharge,
            },
            notes=f"settlement deducted {surcharge} paise more fee than the fee record",
        )

    def tax_mismatch(self, pid: str) -> Injection:
        """GST deducted at the wrong rate - typically 28% instead of 18%."""
        line = self.payment_line[pid]
        fee = self.fees[pid]
        wrong_rate = self.rng.choice([2_800, 1_200, 500])
        wrong_tax = fee["fee_amount"] * wrong_rate // BPS
        drift = wrong_tax - fee["tax_amount"]
        if drift == 0:
            drift = 100
            wrong_tax = fee["tax_amount"] + drift
        line["debit_tax"] = wrong_tax
        self._retune_line(line, line["net_amount"] - drift)
        return Injection(
            reason_code=ExceptionType.TAX_MISMATCH.value,
            explained_amount=-drift,
            params={
                "recorded_tax": fee["tax_amount"],
                "deducted_tax": wrong_tax,
                "applied_rate_bps": wrong_rate,
            },
            notes=f"GST deducted at {wrong_rate / 100:.0f}% instead of "
            f"{self.cfg.gst_rate_bps / 100:.0f}%",
        )

    def partial_settlement(self, pid: str) -> Injection:
        """Part of the payment settled; the remainder sits in a pending batch.

        Only representable because migration 0005 replaced the one-line-per-
        payment index with an over-settlement trigger.
        """
        line = self.payment_line[pid]
        payment = self.payments[pid]
        held = max(100, payment["amount"] * self.rng.randint(2_000, 6_000) // BPS)
        line["credit_amount"] -= held
        self._retune_line(line, line["net_amount"] - held)

        pending = self._future_batch()
        remainder = {
            "item_id": self.ids.mint("si"),
            "settlement_id": pending["settlement_id"],
            "item_type": SettlementItemType.PAYMENT.value,
            "payment_id": pid,
            "refund_id": None,
            "adjustment_id": None,
            "credit_amount": held,
            "debit_fee": 0,
            "debit_tax": 0,
            "net_amount": held,
            "created_at": _at_noon(self.as_of),
        }
        self.world.settlement_items.append(remainder)
        self._shift_batch(pending["settlement_id"], held)
        return Injection(
            reason_code=ExceptionType.PARTIAL_SETTLEMENT.value,
            explained_amount=-held,
            params={"held_back": held, "pending_batch": pending["settlement_id"]},
            notes=f"{held} paise held back into an unprocessed batch",
        )

    def settlement_timing(self, pid: str) -> Injection:
        """The money arrived, but well after the cycle allowed.

        The amount is right, so the delta is zero - this is a date exception,
        found by comparing the settlement date against the payment's deadline.
        """
        line = self.payment_line[pid]
        payment = self.payments[pid]
        late_by = self.rng.randint(4, 20)
        late_day = min(
            self.as_of, settlement_eligible_on(payment["captured_at"], self.cfg)
            + timedelta(days=late_by),
        )
        late_batch = self._batch_on(late_day)
        self._shift_batch(line["settlement_id"], -line["net_amount"])
        line["settlement_id"] = late_batch["settlement_id"]
        self._shift_batch(late_batch["settlement_id"], line["net_amount"])
        return Injection(
            reason_code=ExceptionType.SETTLEMENT_TIMING.value,
            explained_amount=0,
            params={"settled_on": late_day.isoformat(), "late_by_days": late_by},
            notes="settled late; amount correct",
            delta_visible=False,
        )

    def unexpected_adjustment(self, pid: str) -> Injection:
        """An unexplained debit against the payout, with no stated reason.

        The arithmetic reconciles - the adjustment is on the books - but nobody
        authorised it. Found by rule: an adjustment with no reason recorded.
        """
        payment = self.payments[pid]
        line = self.payment_line[pid]
        amount = -max(
            500, payment["amount"] * self.rng.randint(300, 2_500) // BPS
        )
        adjustment_id = self.ids.mint("adj")
        self.world.adjustments.append(
            {
                "adjustment_id": adjustment_id,
                "settlement_id": line["settlement_id"],
                "payment_id": pid,
                "amount": amount,
                "type": AdjustmentType.MANUAL_DEBIT.value,
                "reason": None,  # the tell
                "created_at": payment["captured_at"] + timedelta(days=1),
            }
        )
        self.world.settlement_items.append(
            {
                "item_id": self.ids.mint("si"),
                "settlement_id": line["settlement_id"],
                "item_type": SettlementItemType.ADJUSTMENT.value,
                "payment_id": None,
                "refund_id": None,
                "adjustment_id": adjustment_id,
                "credit_amount": 0,
                "debit_fee": 0,
                "debit_tax": 0,
                "net_amount": amount,
                "created_at": line["created_at"],
            }
        )
        self._shift_batch(line["settlement_id"], amount)
        return Injection(
            reason_code=ExceptionType.UNEXPECTED_ADJUSTMENT.value,
            explained_amount=amount,
            params={"adjustment_id": adjustment_id, "amount": amount},
            notes="debit against the payout with no reason recorded",
            delta_visible=False,
        )

    def unknown_discrepancy(self, pid: str) -> Injection:
        """Money is missing and nothing in the data explains it.

        This is the category the agent must refuse to close. There is no record
        anywhere that accounts for the shortfall, because none was created.
        """
        line = self.payment_line[pid]
        payment = self.payments[pid]
        shortfall = max(
            500, payment["amount"] * self.rng.randint(500, 3_500) // BPS
        )
        self._retune_line(line, line["net_amount"] - shortfall)
        return Injection(
            reason_code=ExceptionType.UNKNOWN_DISCREPANCY.value,
            explained_amount=None,  # deliberately unexplainable
            params={"shortfall": shortfall},
            notes="unexplained shortfall; no supporting record exists",
        )

    # -- the hard families -------------------------------------------------

    def multi_cause(self, pid: str) -> Injection:
        """Two independent faults inside one discrepancy.

        A single-hypothesis matcher tests each cause against the whole delta,
        finds no exact match, and gives up. Closing this requires decomposing
        the delta rather than pattern-matching it.
        """
        first = self.fee_mismatch(pid)
        second = (
            self.incorrect_refund_amount(pid)
            if self.refunds_by_payment[pid]
            and any(
                r["refund_id"] in self.refund_line
                for r in self.refunds_by_payment[pid]
            )
            else self.tax_mismatch(pid)
        )
        explained = (first.explained_amount or 0) + (second.explained_amount or 0)
        return Injection(
            reason_code=FAMILY_MULTI_CAUSE,
            explained_amount=explained,
            params={
                "causes": [first.reason_code, second.reason_code],
                "components": {
                    first.reason_code: first.explained_amount,
                    second.reason_code: second.explained_amount,
                },
                **{f"first_{k}": v for k, v in first.params.items()},
                **{f"second_{k}": v for k, v in second.params.items()},
            },
            notes=f"{first.reason_code} and {second.reason_code} in one discrepancy",
        )

    def cross_entity(self, pid: str) -> Injection:
        """The explanation exists, but not where a rule would look for it.

        The shortfall is a recovery recorded as an adjustment that carries no
        `payment_id` - it names the payment only in its free-text reason and is
        reachable only via the settlement batch. Any rule keyed on
        `adjustment.payment_id` finds nothing; the agent has to hop from the
        payment to its batch and read what else is there.
        """
        line = self.payment_line[pid]
        payment = self.payments[pid]
        recovered = max(
            500, payment["amount"] * self.rng.randint(400, 2_000) // BPS
        )
        self._retune_line(line, line["net_amount"] - recovered)

        adjustment_id = self.ids.mint("adj")
        self.world.adjustments.append(
            {
                "adjustment_id": adjustment_id,
                "settlement_id": line["settlement_id"],
                "payment_id": None,  # the hop the rules cannot make
                "amount": -recovered,
                "type": AdjustmentType.PLATFORM_FEE.value,
                "reason": f"recovery applied against {pid}",
                "created_at": payment["captured_at"] + timedelta(days=1),
            }
        )
        return Injection(
            reason_code=FAMILY_CROSS_ENTITY,
            explained_amount=-recovered,
            params={
                "adjustment_id": adjustment_id,
                "amount": -recovered,
                "reachable_via": "settlement batch, not payment_id",
            },
            notes="recovery recorded against the batch, not linked to the payment",
        )

    def timing_shifted(self, pid: str) -> Injection:
        """A refund that is due, but whose debit sits in a batch not yet paid.

        The delta looks exactly like a missing refund. It is not: the debit
        exists and is scheduled. Telling the two apart requires looking at
        batch status, which is precisely the check a naive matcher skips.
        """
        refund, line = self._settled_refund(pid)
        pending = self._future_batch(days_ahead=self.rng.randint(2, 6))
        self._shift_batch(line["settlement_id"], -line["net_amount"])
        line["settlement_id"] = pending["settlement_id"]
        self._shift_batch(pending["settlement_id"], line["net_amount"])
        return Injection(
            reason_code=FAMILY_TIMING_SHIFTED,
            explained_amount=refund["amount"],
            params={
                "refund_id": refund["refund_id"],
                "pending_batch": pending["settlement_id"],
                "amount": refund["amount"],
            },
            notes="refund debit scheduled into a batch that has not been processed",
        )

    # -- batch helpers -----------------------------------------------------

    def _batch_on(self, day: date) -> dict:
        """The processed batch for a date, created if the date has no batch."""
        for settlement in self.world.settlements:
            if (
                settlement["settlement_date"].date() == day
                and settlement["status"] == SettlementStatus.PROCESSED.value
            ):
                return settlement
        settlement = {
            "settlement_id": self.ids.mint("setl"),
            "net_amount": 0,
            "utr": f"UTR{self.rng.randrange(10**11, 10**12)}",
            "status": SettlementStatus.PROCESSED.value,
            "settlement_date": _at_noon(day),
            "created_at": _at_noon(day),
        }
        self.world.settlements.append(settlement)
        self.settlements[settlement["settlement_id"]] = settlement
        return settlement

    # ==================================================================

    def run(self, *, rate_bps: int, mix: dict[str, int] | None = None) -> InjectionReport:
        mix = mix or DEFAULT_MIX
        report = InjectionReport()
        target = len(self.world.payments) * rate_bps // BPS

        kinds = list(mix)
        weights = [mix[k] for k in kinds]

        while report.total() < target:
            report.attempted += 1
            if report.attempted > target * 20:
                break  # the pool is exhausted; stop rather than loop
            kind = self.rng.choices(kinds, weights=weights, k=1)[0]
            injector, needs_refund = INJECTORS[kind]
            pool = self._candidates(needs_refund=needs_refund)
            if not pool:
                report.skipped += 1
                continue
            pid = self.rng.choice(pool)
            result = injector(self, pid)
            self.claimed.add(pid)
            if not result.mark_source:
                report.injected[result.reason_code] += 1
                continue
            truth = self.truths[pid]
            truth.update(
                {
                    "is_exception": True,
                    "reason_code": result.reason_code,
                    "explained_amount": result.explained_amount,
                    "injection_params": {
                        **result.params,
                        "delta_visible": result.delta_visible,
                    },
                    "notes": result.notes,
                }
            )
            report.injected[result.reason_code] += 1

        self.world.stats["injection"] = dict(report.injected)
        self.world.stats["exceptions_injected"] = report.total()
        return report


def _at_noon(day: date):
    from datetime import datetime, time, timezone

    return datetime.combine(day, time(12, 0), timezone.utc)


#: name -> (bound method, requires a settled refund)
INJECTORS: dict[str, tuple[Callable[[ExceptionInjector, str], Injection], bool]] = {
    ExceptionType.MISSING_SETTLEMENT.value: (ExceptionInjector.missing_settlement, False),
    ExceptionType.DUPLICATE_PAYMENT.value: (ExceptionInjector.duplicate_payment, False),
    ExceptionType.MISSING_REFUND.value: (ExceptionInjector.missing_refund, True),
    ExceptionType.INCORRECT_REFUND_AMOUNT.value: (
        ExceptionInjector.incorrect_refund_amount,
        True,
    ),
    ExceptionType.FEE_MISMATCH.value: (ExceptionInjector.fee_mismatch, False),
    ExceptionType.TAX_MISMATCH.value: (ExceptionInjector.tax_mismatch, False),
    ExceptionType.PARTIAL_SETTLEMENT.value: (ExceptionInjector.partial_settlement, False),
    ExceptionType.SETTLEMENT_TIMING.value: (ExceptionInjector.settlement_timing, False),
    ExceptionType.UNEXPECTED_ADJUSTMENT.value: (
        ExceptionInjector.unexpected_adjustment,
        False,
    ),
    ExceptionType.UNKNOWN_DISCREPANCY.value: (
        ExceptionInjector.unknown_discrepancy,
        False,
    ),
    FAMILY_MULTI_CAUSE: (ExceptionInjector.multi_cause, False),
    FAMILY_CROSS_ENTITY: (ExceptionInjector.cross_entity, False),
    FAMILY_TIMING_SHIFTED: (ExceptionInjector.timing_shifted, True),
}

#: Weighted towards the mundane, as a real exception queue is. The hard
#: families together are ~22% - enough to measure the AI's advantage without
#: pretending most finance work is exotic.
DEFAULT_MIX: dict[str, int] = {
    ExceptionType.MISSING_SETTLEMENT.value: 10,
    ExceptionType.DUPLICATE_PAYMENT.value: 8,
    ExceptionType.MISSING_REFUND.value: 8,
    ExceptionType.INCORRECT_REFUND_AMOUNT.value: 8,
    ExceptionType.FEE_MISMATCH.value: 12,
    ExceptionType.TAX_MISMATCH.value: 8,
    ExceptionType.PARTIAL_SETTLEMENT.value: 7,
    ExceptionType.SETTLEMENT_TIMING.value: 7,
    ExceptionType.UNEXPECTED_ADJUSTMENT.value: 5,
    ExceptionType.UNKNOWN_DISCREPANCY.value: 5,
    FAMILY_MULTI_CAUSE: 8,
    FAMILY_CROSS_ENTITY: 7,
    FAMILY_TIMING_SHIFTED: 7,
}


def inject_exceptions(
    world: World,
    *,
    cfg: FinancialConfig,
    as_of: date,
    seed: int,
    rate_bps: int = 700,
    mix: dict[str, int] | None = None,
) -> InjectionReport:
    return ExceptionInjector(world, cfg=cfg, as_of=as_of, seed=seed).run(
        rate_bps=rate_bps, mix=mix
    )
