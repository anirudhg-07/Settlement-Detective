"""Evaluation metrics — aggregates only.

This is the one endpoint that touches ground truth, and it touches it through
the `sd_eval` role to produce counts. It never returns a per-case answer key.

That line matters. An operations team legitimately needs to know how well
detection is performing. Nobody needs an HTTP route that says "payment X was
deliberately broken in this way" - publishing that would make every accuracy
figure this project reports meaningless, because the agent could read it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import Connection, text

from backend.api.deps import connection, eval_connection, latest_run_id
from backend.api.schemas import (
    ClassificationMetrics,
    DetectionMetrics,
    InvestigationMetrics,
    Metrics,
)
from backend.reconciliation.classifier import FAMILY_TO_TYPE

router = APIRouter(tags=["metrics"])


def _bps(numerator: int, denominator: int) -> int:
    return (numerator * 10_000 // denominator) if denominator else 0


@router.get("/metrics", response_model=Metrics)
def metrics(
    run_id: str | None = None,
    conn: Connection = Depends(connection),
    ev: Connection = Depends(eval_connection),
    default_run: str = Depends(latest_run_id),
) -> Metrics:
    run = run_id or default_run

    header = conn.execute(
        text("SELECT * FROM recon.recon_runs WHERE run_id = :r"), {"r": run}
    ).mappings().one()
    processed = header["records_processed"] or 0
    reconciled = (header["matched_count"] or 0) + (header["pending_count"] or 0)
    seconds = (
        (header["completed_at"] - header["started_at"]).total_seconds()
        if header["completed_at"] else 0.0
    )

    # ---- detection, against ground truth --------------------------------
    truth = {
        r.payment_id: r.reason_code
        for r in ev.execute(
            text("SELECT payment_id, reason_code FROM gt.case_truth WHERE is_exception")
        )
    }
    detected = {
        r.payment_id: r.exception_type
        for r in ev.execute(
            text(
                "SELECT payment_id, exception_type FROM recon.exceptions"
                " WHERE run_id = :r"
            ),
            {"r": run},
        )
    }
    injected, found = set(truth), set(detected)
    true_positives = injected & found

    # ---- classification --------------------------------------------------
    correct = incorrect = unscoreable = 0
    for payment_id, reason in truth.items():
        if payment_id not in detected:
            continue
        want = FAMILY_TO_TYPE.get(reason, reason)
        if want is None:
            # MULTI_CAUSE has no single correct type by construction.
            unscoreable += 1
        elif detected[payment_id] == (getattr(want, "value", want)):
            correct += 1
        else:
            incorrect += 1

    # ---- investigations --------------------------------------------------
    rows = conn.execute(
        text(
            "SELECT i.final_status, i.tool_call_count, i.evidence_score,"
            "       i.reasoning_confidence FROM recon.investigations i"
            "  JOIN recon.exceptions e USING (exception_id)"
            " WHERE e.run_id = :r"
        ),
        {"r": run},
    ).mappings().all()

    by_status: dict[str, int] = {}
    for row in rows:
        by_status[row["final_status"] or "UNKNOWN"] = (
            by_status.get(row["final_status"] or "UNKNOWN", 0) + 1
        )
    scored = [r for r in rows if r["evidence_score"] is not None]
    gaps = [
        r["reasoning_confidence"] - r["evidence_score"]
        for r in rows
        if r["reasoning_confidence"] is not None and r["evidence_score"] is not None
    ]

    investigated = len(rows)
    note = (
        f"Investigation figures cover {investigated} case(s). "
        "Below a few hundred these are a smoke test, not an evaluation - "
        "the full run is Phase 14."
    ) if investigated < 100 else "Investigation figures cover the full sample."

    return Metrics(
        run_id=run,
        records_processed=processed,
        reconciled_bps=_bps(reconciled, processed),
        throughput_per_second=round(processed / seconds, 1) if seconds else 0.0,
        batches_out_of_balance=header["batches_out_of_balance"] or 0,
        detection=DetectionMetrics(
            injected=len(injected),
            detected=len(found),
            true_positives=len(true_positives),
            false_positives=len(found - injected),
            missed=len(injected - found),
            precision_bps=_bps(len(true_positives), len(found)),
            recall_bps=_bps(len(true_positives), len(injected)),
        ),
        classification=ClassificationMetrics(
            scoreable=correct + incorrect,
            correct=correct,
            incorrect=incorrect,
            no_single_correct_type=unscoreable,
            accuracy_bps=_bps(correct, correct + incorrect),
        ),
        investigation=InvestigationMetrics(
            investigated=investigated,
            by_status=by_status,
            mean_tool_calls=round(
                sum(r["tool_call_count"] or 0 for r in rows) / investigated, 2
            ) if investigated else 0.0,
            mean_evidence_score=round(
                sum(r["evidence_score"] for r in scored) / len(scored), 1
            ) if scored else 0.0,
            mean_confidence_overclaim=round(sum(gaps) / len(gaps), 1) if gaps else 0.0,
        ),
        note=note,
    )
