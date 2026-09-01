"""Reconciliation runs — the Command Centre's numbers."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import Connection, text

from backend.api.deps import connection, latest_run_id
from backend.api.schemas import Health, RunSummary

router = APIRouter(tags=["runs"])


def _summarise(row) -> RunSummary:
    processed = row["records_processed"] or 0
    reconciled = (row["matched_count"] or 0) + (row["pending_count"] or 0)
    return RunSummary(
        run_id=row["run_id"],
        as_of_date=row["as_of_date"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        records_processed=processed,
        matched=row["matched_count"] or 0,
        pending=row["pending_count"] or 0,
        exceptions=row["exception_count"] or 0,
        batches_checked=row["batches_checked"] or 0,
        batches_out_of_balance=row["batches_out_of_balance"] or 0,
        match_rate_bps=(reconciled * 10_000 // processed) if processed else 0,
        tolerance_paise=row["tolerance_paise"],
    )


@router.get("/health", response_model=Health)
def health(conn: Connection = Depends(connection)) -> Health:
    payments = conn.execute(text("SELECT count(*) FROM ops.payments")).scalar() or 0
    run = conn.execute(
        text("SELECT run_id FROM recon.recon_runs ORDER BY started_at DESC LIMIT 1")
    ).scalar()
    return Health(
        status="ok",
        database=True,
        dataset_loaded=payments > 0,
        payments=payments,
        latest_run=run,
    )


@router.get("/runs", response_model=list[RunSummary])
def list_runs(limit: int = 20, conn: Connection = Depends(connection)):
    rows = conn.execute(
        text(
            "SELECT * FROM recon.recon_runs ORDER BY started_at DESC LIMIT :n"
        ),
        {"n": min(limit, 100)},
    ).mappings().all()
    return [_summarise(r) for r in rows]


@router.get("/runs/latest", response_model=RunSummary)
def latest(
    run_id: str = Depends(latest_run_id), conn: Connection = Depends(connection)
) -> RunSummary:
    row = conn.execute(
        text("SELECT * FROM recon.recon_runs WHERE run_id = :r"), {"r": run_id}
    ).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"no run {run_id}")
    return _summarise(row)
