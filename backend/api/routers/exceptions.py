"""The exception queue and one exception's full story.

Reads only. Investigations are recorded here, never edited: the detail endpoint
reassembles what Phase 10 already stores, including the integrity check on the
audit chain.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Connection, text

from backend.api.deps import connection, latest_run_id
from backend.api.schemas import (
    EvidenceItem,
    ExceptionDetail,
    ExceptionPage,
    ExceptionSummary,
    IntegrityCheck,
    InvestigationDetail,
    Money,
    ScoreFactor,
    StepItem,
)
from backend.audit.trail import reconstruct

router = APIRouter(prefix="/exceptions", tags=["exceptions"])


def _summary(row) -> ExceptionSummary:
    return ExceptionSummary(
        exception_id=row["exception_id"],
        payment_id=row["payment_id"],
        exception_type=row["exception_type"],
        status=row["status"],
        detected_by=row["detected_by"],
        expected_net=Money.of(row["expected_net"]),
        actual_net=Money.of(row["actual_net"]),
        delta=Money.of(row["delta"]),
        evidence_score=row["evidence_score"],
        created_at=row["created_at"],
    )


@router.get("", response_model=ExceptionPage)
def list_exceptions(
    run_id: str | None = None,
    status: list[str] | None = Query(default=None),
    exception_type: list[str] | None = Query(default=None),
    detected_by: str | None = None,
    min_abs_delta: int | None = Query(default=None, description="paise"),
    max_evidence_score: int | None = None,
    limit: int = Query(default=50, le=500),
    offset: int = 0,
    conn: Connection = Depends(connection),
    default_run: str = Depends(latest_run_id),
) -> ExceptionPage:
    """The queue, with the filters Screen 2 needs."""
    where = ["e.run_id = :run_id"]
    params: dict = {"run_id": run_id or default_run}
    if status:
        where.append("e.status = ANY(:status)")
        params["status"] = status
    if exception_type:
        where.append("e.exception_type = ANY(:etypes)")
        params["etypes"] = exception_type
    if detected_by:
        where.append("e.detected_by = :detected_by")
        params["detected_by"] = detected_by
    if min_abs_delta is not None:
        where.append("abs(e.delta) >= :min_delta")
        params["min_delta"] = min_abs_delta
    if max_evidence_score is not None:
        where.append("e.evidence_score IS NOT NULL AND e.evidence_score <= :max_score")
        params["max_score"] = max_evidence_score
    clause = " AND ".join(where)

    total = conn.execute(
        text(f"SELECT count(*) FROM recon.exceptions e WHERE {clause}"), params
    ).scalar() or 0
    rows = conn.execute(
        text(
            f"SELECT * FROM recon.exceptions e WHERE {clause}"
            " ORDER BY abs(e.delta) DESC, e.exception_id LIMIT :limit OFFSET :offset"
        ),
        {**params, "limit": limit, "offset": offset},
    ).mappings().all()
    return ExceptionPage(
        items=[_summary(r) for r in rows], total=total, limit=limit, offset=offset
    )


@router.get("/{exception_id}", response_model=ExceptionDetail)
def get_exception(
    exception_id: str, conn: Connection = Depends(connection)
) -> ExceptionDetail:
    trail = reconstruct(conn, exception_id)
    if trail is None:
        raise HTTPException(status_code=404, detail=f"no exception {exception_id}")

    row = conn.execute(
        text("SELECT * FROM recon.exceptions WHERE exception_id = :e"),
        {"e": exception_id},
    ).mappings().one()

    investigation = None
    if trail.investigation:
        i = trail.investigation
        investigation = InvestigationDetail(
            investigation_id=i["investigation_id"],
            mode=i["mode"],
            llm_model=i.get("llm_model"),
            prompt_version=i.get("prompt_version"),
            decision=i.get("decision"),
            final_status=i.get("final_status"),
            unexplained_amount=Money.of(i.get("unexplained_amount")),
            evidence_score=i.get("evidence_score"),
            reasoning_confidence=i.get("reasoning_confidence"),
            tool_call_count=i.get("tool_call_count") or 0,
            latency_ms=i.get("latency_ms"),
            records_examined=trail.records_examined,
            score_factors=[ScoreFactor(**f) for f in (i.get("score_factors") or [])],
            steps=[
                StepItem(
                    seq=s["seq"], step_type=s["step_type"], tool_name=s["tool_name"],
                    tool_args=s["tool_args"], observation=s["observation"],
                    duration_ms=s["duration_ms"],
                )
                for s in trail.steps
            ],
            evidence=[
                EvidenceItem(
                    record_type=e["record_type"], record_id=e["record_id"],
                    role=e["role"],
                    amount_contribution=Money.of(e["amount_contribution"]),
                    note=e["note"],
                )
                for e in trail.evidence
            ],
            integrity=(
                IntegrityCheck(
                    steps_checked=trail.chain.steps_checked,
                    intact=trail.chain.intact,
                    broken_at=trail.chain.broken_at,
                    detail=trail.chain.detail,
                )
                if trail.chain else None
            ),
        )

    return ExceptionDetail(
        exception=_summary(row),
        timeline=trail.timeline() if trail.investigation else ["DETECTED"],
        investigation=investigation,
    )
