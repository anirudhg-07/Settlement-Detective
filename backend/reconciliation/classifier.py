"""Exception classification - the deterministic baseline.

Two stages, and they answer different questions.

**Classifying a delta.** For each detected discrepancy, build the candidate
causes the records actually support, and see which one accounts for the delta
*exactly*. One exact match is a classification; anything else is not, and is
reported as ``UNKNOWN_DISCREPANCY`` rather than guessed at.

**Finding what has no delta at all.** A duplicate charge reconciles perfectly -
the money adds up, it simply should never have moved. Arithmetic is blind to
it, so three rules look for it directly.

A deliberate limitation, stated rather than hidden: this classifier tests
single hypotheses. When two faults share one discrepancy, no individual cause
matches the delta and the baseline correctly declines to answer. Searching
combinations is where an investigating agent earns its place, and leaving that
gap honest is the point - a baseline rigged to fail would make Phase 14's
comparison worthless, and one rigged to succeed would hide the gap entirely.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from sqlalchemy import Connection, text

from backend.config import FinancialConfig
from backend.enums import (
    DetectedBy,
    ExceptionStatus,
    ExceptionType,
    PaymentStatus,
    RefundStatus,
    SettlementItemType,
    SettlementStatus,
)
from backend.models import Exception_
from backend.reconciliation.engine import mint_exception_id
from backend.reconciliation.settlement_math import component_is_due
from backend.reconciliation.timing import settlement_deadline

#: Ground truth records the injected *family* for the hard cases. These are the
#: taxonomy types a correct classification maps onto. MULTI_CAUSE has no single
#: correct type by construction - that is what makes it hard.
FAMILY_TO_TYPE: dict[str, ExceptionType | None] = {
    "TIMING_SHIFTED": ExceptionType.SETTLEMENT_TIMING,
    "CROSS_ENTITY": ExceptionType.UNEXPECTED_ADJUSTMENT,
    "MULTI_CAUSE": None,
}


@dataclass
class Hypothesis:
    """A candidate cause and the signed paise it would account for."""

    exception_type: ExceptionType
    explains: int
    evidence: dict = field(default_factory=dict)


@dataclass
class Classification:
    payment_id: str
    exception_type: ExceptionType
    explains: int | None
    hypotheses: list[Hypothesis]
    #: True when no single cause matched but the candidates together would.
    #: The signature of a multi-cause discrepancy, recorded so the baseline's
    #: blind spot is measurable rather than invisible.
    combination_would_explain: bool = False


@dataclass
class ClassificationSummary:
    run_id: str
    classified: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    rule_detected: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    combination_cases: int = 0

    def total_classified(self) -> int:
        return sum(self.classified.values())

    def total_rule_detected(self) -> int:
        return sum(self.rule_detected.values())


# --------------------------------------------------------------------------
# Context loading
# --------------------------------------------------------------------------


@dataclass
class Context:
    payments: dict[str, dict]
    fees: dict[str, dict]
    refunds: dict[str, list[dict]]
    adjustments: dict[str, list[dict]]
    payment_lines: dict[str, list[dict]]
    refund_lines: dict[str, list[dict]]
    #: Adjustments with no payment linkage, grouped by the batch they sit in.
    unlinked_by_batch: dict[str, list[dict]]
    payments_by_order: dict[str, list[dict]]


def load_context(conn: Connection) -> Context:
    payments = {
        r["payment_id"]: dict(r)
        for r in conn.execute(
            text(
                "SELECT payment_id, order_id, customer_id, amount, payment_method,"
                "       status, created_at, captured_at FROM ops.payments"
            )
        ).mappings()
    }
    fees = {
        r["payment_id"]: dict(r)
        for r in conn.execute(
            text("SELECT payment_id, fee_amount, tax_amount FROM ops.fees")
        ).mappings()
    }

    refunds: dict[str, list[dict]] = defaultdict(list)
    refund_owner: dict[str, str] = {}
    for r in conn.execute(
        text(
            "SELECT refund_id, payment_id, amount, status, processed_at FROM ops.refunds"
        )
    ).mappings():
        refunds[r["payment_id"]].append(dict(r))
        refund_owner[r["refund_id"]] = r["payment_id"]

    adjustments: dict[str, list[dict]] = defaultdict(list)
    unlinked: list[dict] = []
    for r in conn.execute(
        text(
            "SELECT adjustment_id, settlement_id, payment_id, amount, type, reason,"
            "       created_at FROM ops.adjustments"
        )
    ).mappings():
        if r["payment_id"]:
            adjustments[r["payment_id"]].append(dict(r))
        else:
            unlinked.append(dict(r))

    payment_lines: dict[str, list[dict]] = defaultdict(list)
    refund_lines: dict[str, list[dict]] = defaultdict(list)
    for r in conn.execute(
        text(
            "SELECT i.item_id, i.item_type, i.payment_id, i.refund_id, i.adjustment_id,"
            "       i.credit_amount, i.debit_fee, i.debit_tax, i.net_amount,"
            "       s.settlement_id, s.status AS batch_status,"
            "       s.settlement_date AS batch_date"
            "  FROM ops.settlement_items i"
            "  JOIN ops.settlements s USING (settlement_id)"
        )
    ).mappings():
        row = dict(r)
        if row["item_type"] == SettlementItemType.PAYMENT.value:
            payment_lines[row["payment_id"]].append(row)
        elif row["item_type"] == SettlementItemType.REFUND.value:
            refund_lines[row["refund_id"]].append(row)

    unlinked_by_batch: dict[str, list[dict]] = defaultdict(list)
    for adjustment in unlinked:
        if adjustment["settlement_id"]:
            unlinked_by_batch[adjustment["settlement_id"]].append(adjustment)

    payments_by_order: dict[str, list[dict]] = defaultdict(list)
    for payment in payments.values():
        payments_by_order[payment["order_id"]].append(payment)

    return Context(
        payments=payments,
        fees=fees,
        refunds=refunds,
        adjustments=adjustments,
        payment_lines=payment_lines,
        refund_lines=refund_lines,
        unlinked_by_batch=unlinked_by_batch,
        payments_by_order=payments_by_order,
    )


def _processed(lines: list[dict]) -> list[dict]:
    return [l for l in lines if l["batch_status"] == SettlementStatus.PROCESSED.value]


def _scheduled(lines: list[dict]) -> list[dict]:
    """Lines sitting in a batch that has not been paid out."""
    return [l for l in lines if l["batch_status"] != SettlementStatus.PROCESSED.value]


# --------------------------------------------------------------------------
# Hypotheses
# --------------------------------------------------------------------------


def build_hypotheses(
    payment_id: str, ctx: Context, cfg: FinancialConfig, as_of: date
) -> list[Hypothesis]:
    """Every cause the records actually support, with what each accounts for."""
    payment = ctx.payments[payment_id]
    lines = ctx.payment_lines.get(payment_id, [])
    settled, scheduled = _processed(lines), _scheduled(lines)
    out: list[Hypothesis] = []

    # -- the money never arrived -----------------------------------------
    if not settled and payment["status"] != PaymentStatus.FAILED.value:
        if payment["captured_at"] and as_of > settlement_deadline(
            payment["captured_at"], cfg
        ):
            fee = ctx.fees.get(payment_id) or {"fee_amount": 0, "tax_amount": 0}
            owed = payment["amount"] - fee["fee_amount"] - fee["tax_amount"]
            out.append(
                Hypothesis(
                    ExceptionType.MISSING_SETTLEMENT,
                    -owed,
                    {"expected_credit": owed, "settled_lines": 0},
                )
            )

    # -- part of it is still scheduled ------------------------------------
    if settled and scheduled:
        held = sum(l["credit_amount"] for l in scheduled)
        out.append(
            Hypothesis(
                ExceptionType.PARTIAL_SETTLEMENT,
                -held,
                {
                    "held_back": held,
                    "pending_batches": [l["settlement_id"] for l in scheduled],
                },
            )
        )

    # -- the gateway deducted a different fee or tax than it recorded ------
    fee = ctx.fees.get(payment_id)
    if fee and settled:
        deducted_fee = sum(l["debit_fee"] for l in settled)
        deducted_tax = sum(l["debit_tax"] for l in settled)
        if deducted_fee != fee["fee_amount"]:
            out.append(
                Hypothesis(
                    ExceptionType.FEE_MISMATCH,
                    -(deducted_fee - fee["fee_amount"]),
                    {"recorded_fee": fee["fee_amount"], "deducted_fee": deducted_fee},
                )
            )
        if deducted_tax != fee["tax_amount"]:
            out.append(
                Hypothesis(
                    ExceptionType.TAX_MISMATCH,
                    -(deducted_tax - fee["tax_amount"]),
                    {"recorded_tax": fee["tax_amount"], "deducted_tax": deducted_tax},
                )
            )

    # -- the refund legs ---------------------------------------------------
    for refund in ctx.refunds.get(payment_id, []):
        if refund["status"] != RefundStatus.PROCESSED.value:
            continue
        if not component_is_due(refund["processed_at"], as_of, cfg):
            continue  # not expected to have been debited yet
        rlines = ctx.refund_lines.get(refund["refund_id"], [])
        debited = _processed(rlines)
        pending = _scheduled(rlines)

        if debited:
            taken = -sum(l["net_amount"] for l in debited)
            if taken != refund["amount"]:
                out.append(
                    Hypothesis(
                        ExceptionType.INCORRECT_REFUND_AMOUNT,
                        refund["amount"] - taken,
                        {
                            "refund_id": refund["refund_id"],
                            "recorded": refund["amount"],
                            "debited": taken,
                        },
                    )
                )
        elif pending:
            # The debit exists and is scheduled - late, not lost. Telling this
            # apart from a missing refund is purely a matter of checking the
            # batch status, and getting it wrong would send a healthy case to a
            # human as a suspected loss.
            out.append(
                Hypothesis(
                    ExceptionType.SETTLEMENT_TIMING,
                    refund["amount"],
                    {
                        "refund_id": refund["refund_id"],
                        "scheduled_in": [l["settlement_id"] for l in pending],
                    },
                )
            )
        else:
            out.append(
                Hypothesis(
                    ExceptionType.MISSING_REFUND,
                    refund["amount"],
                    {"refund_id": refund["refund_id"], "amount": refund["amount"]},
                )
            )

    # -- a deduction booked against the batch, not against the payment -----
    # Reachable only by looking at what else is in the payment's own batch.
    # Required to be a unique exact match: several candidate adjustments of the
    # same size would make the attribution a guess, and a guess is not a
    # classification.
    for line in settled:
        candidates = [
            adjustment
            for adjustment in ctx.unlinked_by_batch.get(line["settlement_id"], [])
        ]
        for adjustment in candidates:
            same_size = [c for c in candidates if c["amount"] == adjustment["amount"]]
            if len(same_size) != 1:
                continue
            out.append(
                Hypothesis(
                    ExceptionType.UNEXPECTED_ADJUSTMENT,
                    adjustment["amount"],
                    {
                        "adjustment_id": adjustment["adjustment_id"],
                        "batch": line["settlement_id"],
                        "reason": adjustment["reason"],
                        "linked_to_payment": False,
                    },
                )
            )

    return out


def classify(
    payment_id: str, delta: int, ctx: Context, cfg: FinancialConfig, as_of: date
) -> Classification:
    hypotheses = build_hypotheses(payment_id, ctx, cfg, as_of)
    tolerance = cfg.tolerance_paise

    exact = [h for h in hypotheses if abs(h.explains - delta) <= tolerance]
    if len(exact) == 1:
        return Classification(payment_id, exact[0].exception_type, exact[0].explains, hypotheses)

    if len(exact) > 1:
        # Two causes each accounting for the whole discrepancy cannot both be
        # true. Picking one would be a coin toss dressed up as a finding.
        return Classification(
            payment_id, ExceptionType.UNKNOWN_DISCREPANCY, None, hypotheses
        )

    combined = sum(h.explains for h in hypotheses)
    return Classification(
        payment_id,
        ExceptionType.UNKNOWN_DISCREPANCY,
        None,
        hypotheses,
        combination_would_explain=bool(hypotheses)
        and abs(combined - delta) <= tolerance,
    )


# --------------------------------------------------------------------------
# Rules for exceptions that have no delta
# --------------------------------------------------------------------------


def find_duplicate_payments(ctx: Context, window_seconds: int = 3_600) -> list[dict]:
    """The same order captured more than once for the same amount.

    Both charges reconcile perfectly. The fault is that the second one exists,
    so the later capture is the one flagged - the first is legitimate.
    """
    out: list[dict] = []
    for order_id, payments in ctx.payments_by_order.items():
        captured = sorted(
            (
                p
                for p in payments
                if p["status"] != PaymentStatus.FAILED.value and p["captured_at"]
            ),
            key=lambda p: p["captured_at"],
        )
        if len(captured) < 2:
            continue
        first = captured[0]
        for later in captured[1:]:
            gap = (later["captured_at"] - first["captured_at"]).total_seconds()
            if later["amount"] == first["amount"] and gap <= window_seconds:
                out.append(
                    {
                        "payment_id": later["payment_id"],
                        "amount": later["amount"],
                        "evidence": {
                            "duplicate_of": first["payment_id"],
                            "order_id": order_id,
                            "seconds_apart": int(gap),
                        },
                    }
                )
    return out


def find_late_settlements(ctx: Context, cfg: FinancialConfig) -> list[dict]:
    """Settled for the right amount, but past the cycle it was owed in."""
    out: list[dict] = []
    for payment_id, lines in ctx.payment_lines.items():
        payment = ctx.payments.get(payment_id)
        if not payment or not payment["captured_at"]:
            continue
        settled = _processed(lines)
        if not settled:
            continue
        deadline = settlement_deadline(payment["captured_at"], cfg)
        latest = max(l["batch_date"].date() for l in settled)
        if latest > deadline:
            out.append(
                {
                    "payment_id": payment_id,
                    "amount": 0,
                    "evidence": {
                        "settled_on": latest.isoformat(),
                        "due_by": deadline.isoformat(),
                        "days_late": (latest - deadline).days,
                    },
                }
            )
    return out


def find_unauthorised_adjustments(ctx: Context) -> list[dict]:
    """A deduction against the payout with no reason recorded.

    The arithmetic is clean - the adjustment is on the books and both sides
    agree. Nobody wrote down why.
    """
    out: list[dict] = []
    for payment_id, adjustments in ctx.adjustments.items():
        for adjustment in adjustments:
            if adjustment["reason"]:
                continue
            out.append(
                {
                    "payment_id": payment_id,
                    "amount": adjustment["amount"],
                    "evidence": {
                        "adjustment_id": adjustment["adjustment_id"],
                        "amount": adjustment["amount"],
                        "type": adjustment["type"],
                        "reason": None,
                    },
                }
            )
    return out


RULES: dict[ExceptionType, str] = {
    ExceptionType.DUPLICATE_PAYMENT: "find_duplicate_payments",
    ExceptionType.SETTLEMENT_TIMING: "find_late_settlements",
    ExceptionType.UNEXPECTED_ADJUSTMENT: "find_unauthorised_adjustments",
}


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def classify_run(
    conn: Connection, *, run_id: str, cfg: FinancialConfig, as_of: date
) -> ClassificationSummary:
    """Type every detected exception, then add the ones with no delta."""
    summary = ClassificationSummary(run_id=run_id)
    ctx = load_context(conn)

    open_exceptions = [
        dict(r)
        for r in conn.execute(
            text(
                "SELECT exception_id, payment_id, delta FROM recon.exceptions"
                " WHERE run_id = :r"
            ),
            {"r": run_id},
        ).mappings()
    ]

    updates = []
    for row in open_exceptions:
        result = classify(row["payment_id"], row["delta"], ctx, cfg, as_of)
        summary.classified[result.exception_type.value] += 1
        summary.combination_cases += int(result.combination_would_explain)
        updates.append(
            {"eid": row["exception_id"], "etype": result.exception_type.value}
        )

    for start in range(0, len(updates), 1_000):
        conn.execute(
            text(
                "UPDATE recon.exceptions SET exception_type = :etype"
                " WHERE exception_id = :eid"
            ),
            updates[start : start + 1_000],
        )

    # -- the rules ---------------------------------------------------------
    already = {row["payment_id"] for row in open_exceptions}
    detectors = {
        ExceptionType.DUPLICATE_PAYMENT: find_duplicate_payments(ctx),
        ExceptionType.SETTLEMENT_TIMING: find_late_settlements(ctx, cfg),
        ExceptionType.UNEXPECTED_ADJUSTMENT: find_unauthorised_adjustments(ctx),
    }

    created_at = datetime.now(timezone.utc)
    new_rows: list[dict] = []
    for exception_type, hits in detectors.items():
        for hit in hits:
            pid = hit["payment_id"]
            if pid in already:
                # A payment that already fails arithmetically is one case, not
                # two. The delta-based classification stands.
                continue
            already.add(pid)
            new_rows.append(
                {
                    # Must include the run: the same payment flagged in two
                    # runs is two exceptions, not a primary-key collision.
                    "exception_id": mint_exception_id(run_id, pid),
                    "run_id": run_id,
                    "payment_id": pid,
                    "exception_type": exception_type.value,
                    "expected_net": 0,
                    "actual_net": 0,
                    "delta": 0,
                    "detected_by": DetectedBy.RULE.value,
                    "status": ExceptionStatus.OPEN.value,
                    "evidence_score": None,
                    "created_at": created_at,
                }
            )
            summary.rule_detected[exception_type.value] += 1

    for start in range(0, len(new_rows), 1_000):
        chunk = new_rows[start : start + 1_000]
        if chunk:
            conn.execute(Exception_.__table__.insert(), chunk)

    conn.execute(
        text(
            "UPDATE recon.recon_runs SET exception_count = ("
            "  SELECT count(*) FROM recon.exceptions WHERE run_id = :r"
            ") WHERE run_id = :r"
        ),
        {"r": run_id},
    )
    return summary
