"""The deterministic reconciliation engine.

Sweeps the database, decides MATCHED / PENDING_SETTLEMENT / EXCEPTION for every
payment, and writes the verdict to ``recon``.

Two things this module deliberately does not do:

* **Arithmetic.** Every number comes from the pure functions in
  ``settlement_math``. This module loads rows, packs them into ``PaymentFacts``,
  and records what comes back. That separation is what lets the property tests
  in G4 stand as a proof about the engine and not merely about a helper.

* **Classification.** It detects that a payment does not reconcile, not why.
  Phase 6 assigns a type and adds the rule-based detectors for the exceptions
  that have no delta at all.

Everything is loaded in one pass of bulk queries rather than per-payment
lookups: at 10,000 payments the whole dataset is a few megabytes, and an
N+1 query pattern would dominate the throughput figure the evaluation reports.
"""

from __future__ import annotations

import hashlib
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from sqlalchemy import Connection, Engine, text

from backend.config import FinancialConfig
from backend.enums import (
    DetectedBy,
    ExceptionStatus,
    PaymentMethod,
    PaymentStatus,
    ReconStatus,
    RefundStatus,
    SettlementItemType,
)
from backend.models import Exception_, ReconResult, ReconRun
from backend.reconciliation.settlement_math import (
    AdjustmentFact,
    PaymentFacts,
    RefundFact,
    ReconOutcome,
    expected_net_settlement,
    reconcile_payment,
)

CHUNK = 2_000


@dataclass
class RunSummary:
    run_id: str
    as_of: date
    records_processed: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    exceptions_written: int = 0
    batches_checked: int = 0
    batches_out_of_balance: list[tuple[str, int]] = field(default_factory=list)
    load_seconds: float = 0.0
    compute_seconds: float = 0.0
    write_seconds: float = 0.0

    @property
    def total_seconds(self) -> float:
        return self.load_seconds + self.compute_seconds + self.write_seconds

    @property
    def throughput(self) -> float:
        return self.records_processed / self.total_seconds if self.total_seconds else 0.0

    @property
    def match_rate_bps(self) -> int:
        """Matched plus legitimately pending, over everything processed."""
        if not self.records_processed:
            return 0
        reconciled = self.counts.get(ReconStatus.MATCHED.value, 0) + self.counts.get(
            ReconStatus.PENDING_SETTLEMENT.value, 0
        )
        return reconciled * 10_000 // self.records_processed


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


@dataclass
class Dataset:
    payments: list[dict]
    fees: dict[str, dict]
    refunds: dict[str, list[dict]]
    adjustments: dict[str, list[dict]]
    actual_net: dict[str, int]
    settled_payments: set[str]


def load_dataset(conn: Connection) -> Dataset:
    """One bulk pass over `ops`. No per-payment queries anywhere."""
    payments = [
        dict(r)
        for r in conn.execute(
            text(
                "SELECT payment_id, order_id, customer_id, amount, payment_method,"
                "       status, captured_at"
                "  FROM ops.payments"
            )
        ).mappings()
    ]

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
    adjustment_owner: dict[str, str] = {}
    for r in conn.execute(
        text(
            "SELECT adjustment_id, payment_id, amount, type, created_at"
            "  FROM ops.adjustments"
        )
    ).mappings():
        # An adjustment with no payment linkage resolves to no payment. That is
        # not an oversight - it is exactly the CROSS_ENTITY shape, and forcing
        # a link here would hand the agent an answer the data does not contain.
        if r["payment_id"]:
            adjustments[r["payment_id"]].append(dict(r))
            adjustment_owner[r["adjustment_id"]] = r["payment_id"]

    # ACTUAL: only lines from batches that have actually been paid out.
    actual_net: dict[str, int] = defaultdict(int)
    settled_payments: set[str] = set()
    for r in conn.execute(
        text(
            "SELECT i.item_type, i.payment_id, i.refund_id, i.adjustment_id,"
            "       i.net_amount"
            "  FROM ops.settlement_items i"
            "  JOIN ops.settlements s USING (settlement_id)"
            " WHERE s.status = 'processed'"
        )
    ).mappings():
        if r["item_type"] == SettlementItemType.PAYMENT.value:
            owner = r["payment_id"]
            settled_payments.add(owner)
        elif r["item_type"] == SettlementItemType.REFUND.value:
            owner = refund_owner.get(r["refund_id"])
        else:
            owner = adjustment_owner.get(r["adjustment_id"])
        if owner:
            actual_net[owner] += r["net_amount"]

    return Dataset(
        payments=payments,
        fees=fees,
        refunds=refunds,
        adjustments=adjustments,
        actual_net=actual_net,
        settled_payments=settled_payments,
    )


def check_batches(conn: Connection) -> tuple[int, list[tuple[str, int]]]:
    """Batch-level identity: a payout IS the sum of its lines. No tolerance."""
    total = conn.execute(text("SELECT count(*) FROM ops.settlements")).scalar_one()
    rows = conn.execute(
        text(
            "SELECT s.settlement_id,"
            "       s.net_amount - COALESCE(SUM(i.net_amount), 0) AS residual"
            "  FROM ops.settlements s"
            "  LEFT JOIN ops.settlement_items i USING (settlement_id)"
            " GROUP BY s.settlement_id, s.net_amount"
            " HAVING s.net_amount <> COALESCE(SUM(i.net_amount), 0)"
        )
    ).all()
    return total, [(r.settlement_id, r.residual) for r in rows]


# --------------------------------------------------------------------------
# Reconciling
# --------------------------------------------------------------------------


def reconcile_dataset(
    data: Dataset, cfg: FinancialConfig, as_of: date
) -> list[ReconOutcome]:
    outcomes: list[ReconOutcome] = []
    for payment in data.payments:
        pid = payment["payment_id"]
        fee = data.fees.get(pid)
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
                for r in data.refunds.get(pid, ())
            ),
            adjustments=tuple(
                AdjustmentFact(
                    a["adjustment_id"], a["amount"], a["type"], created_at=a["created_at"]
                )
                for a in data.adjustments.get(pid, ())
            ),
        )
        expected = expected_net_settlement(facts, cfg, as_of=as_of)
        outcomes.append(
            reconcile_payment(
                expected,
                actual_net=data.actual_net.get(pid, 0),
                has_settled_items=pid in data.settled_payments,
                captured_at=payment["captured_at"],
                as_of=as_of,
                cfg=cfg,
            )
        )
    return outcomes


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def _mint_run_id(as_of: date, started_at: datetime) -> str:
    return f"run_{started_at:%Y%m%d%H%M%S}_{as_of:%Y%m%d}"


def _mint_exception_id(run_id: str, payment_id: str) -> str:
    """Deterministic, so re-running the same data yields the same ids."""
    digest = hashlib.sha1(f"{run_id}:{payment_id}".encode()).hexdigest()
    return f"EX-{digest[:10].upper()}"


def run_reconciliation(
    target: Engine | Connection,
    *,
    cfg: FinancialConfig,
    as_of: date,
    commit: bool = True,
) -> RunSummary:
    """Reconcile every payment and persist the run, its results and exceptions."""
    if isinstance(target, Engine):
        with target.begin() as conn:
            return _run(conn, cfg=cfg, as_of=as_of)
    summary = _run(target, cfg=cfg, as_of=as_of)
    if commit:
        target.commit()
    return summary


def _run(conn: Connection, *, cfg: FinancialConfig, as_of: date) -> RunSummary:
    started_at = datetime.now(timezone.utc)
    run_id = _mint_run_id(as_of, started_at)
    summary = RunSummary(run_id=run_id, as_of=as_of)

    conn.execute(
        ReconRun.__table__.insert(),
        {
            "run_id": run_id,
            "as_of_date": as_of,
            "tolerance_paise": cfg.tolerance_paise,
            "config_snapshot": cfg.snapshot(),
            "started_at": started_at,
            "completed_at": None,
            "records_processed": 0,
        },
    )

    t0 = time.perf_counter()
    data = load_dataset(conn)
    summary.load_seconds = time.perf_counter() - t0

    t1 = time.perf_counter()
    outcomes = reconcile_dataset(data, cfg, as_of)
    summary.compute_seconds = time.perf_counter() - t1

    counts: dict[str, int] = defaultdict(int)
    for outcome in outcomes:
        counts[outcome.status.value] += 1
    summary.counts = dict(counts)
    summary.records_processed = len(outcomes)

    t2 = time.perf_counter()
    computed_at = datetime.now(timezone.utc)
    results = [
        {
            "result_id": f"res_{run_id}_{i}",
            "run_id": run_id,
            "payment_id": o.payment_id,
            "expected_net": o.expected_net,
            "actual_net": o.actual_net,
            "delta": o.delta,
            "status": o.status.value,
            "flags": [f.value for f in o.flags] or None,
            "computed_at": computed_at,
        }
        for i, o in enumerate(outcomes)
    ]
    _bulk(conn, ReconResult, results)

    exceptions = [
        {
            "exception_id": _mint_exception_id(run_id, o.payment_id),
            "run_id": run_id,
            "payment_id": o.payment_id,
            "exception_type": None,  # Phase 6 classifies
            "expected_net": o.expected_net,
            "actual_net": o.actual_net,
            "delta": o.delta,
            "detected_by": DetectedBy.RESIDUAL.value,
            "status": ExceptionStatus.OPEN.value,
            "evidence_score": None,
            "created_at": computed_at,
        }
        for o in outcomes
        if o.is_exception
    ]
    _bulk(conn, Exception_, exceptions)
    summary.exceptions_written = len(exceptions)

    summary.batches_checked, summary.batches_out_of_balance = check_batches(conn)

    conn.execute(
        text(
            "UPDATE recon.recon_runs SET completed_at = :done,"
            " records_processed = :n, matched_count = :m, pending_count = :p,"
            " exception_count = :e, batches_checked = :b, batches_out_of_balance = :ob"
            " WHERE run_id = :run_id"
        ),
        {
            "done": datetime.now(timezone.utc),
            "n": summary.records_processed,
            "m": counts.get(ReconStatus.MATCHED.value, 0),
            "p": counts.get(ReconStatus.PENDING_SETTLEMENT.value, 0),
            "e": counts.get(ReconStatus.EXCEPTION.value, 0),
            "b": summary.batches_checked,
            "ob": len(summary.batches_out_of_balance),
            "run_id": run_id,
        },
    )
    summary.write_seconds = time.perf_counter() - t2
    return summary


def _bulk(conn: Connection, model, rows: list[dict]) -> None:
    for start in range(0, len(rows), CHUNK):
        chunk = rows[start : start + CHUNK]
        if chunk:
            conn.execute(model.__table__.insert(), chunk)
