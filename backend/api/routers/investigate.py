"""Run the AI investigator on one exception.

The only endpoint that spends money and time, so it is deliberately awkward to
spend twice: a case that already has an investigation returns the stored one
unless `force=true` is passed. Repeat calls otherwise replay from the response
cache and cost nothing.

The agent runs on its own least-privilege connection - read-only over `ops`,
no grant on ground truth - exactly as it does from the CLI. The API does not
hand it wider access just because the request arrived over HTTP.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import Connection, text

from backend.agents.investigator import investigate as run_investigation
from backend.agents.investigator import persist
from backend.agents.llm import LLMError, build_provider
from backend.api.deps import as_of_date, connection, financial_config, settings
from backend.api.routers.exceptions import get_exception
from backend.api.schemas import ExceptionDetail
from backend.config import FinancialConfig, Settings

router = APIRouter(prefix="/exceptions", tags=["investigation"])


@router.post("/{exception_id}/investigate", response_model=ExceptionDetail)
def investigate_exception(
    exception_id: str,
    force: bool = False,
    conn: Connection = Depends(connection),
    config: Settings = Depends(settings),
    cfg: FinancialConfig = Depends(financial_config),
    as_of: date = Depends(as_of_date),
) -> ExceptionDetail:
    row = conn.execute(
        text(
            "SELECT exception_id, payment_id, delta, exception_type"
            "  FROM recon.exceptions WHERE exception_id = :e"
        ),
        {"e": exception_id},
    ).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"no exception {exception_id}")

    already = conn.execute(
        text(
            "SELECT 1 FROM recon.investigations WHERE exception_id = :e LIMIT 1"
        ),
        {"e": exception_id},
    ).scalar()
    if already and not force:
        # Cheaper and more honest than silently re-running: the stored result
        # is the one the audit trail already commits to.
        return get_exception(exception_id, conn)

    try:
        provider = build_provider(config)
    except LLMError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    from backend.db.session import agent_engine, owner_engine

    with agent_engine().connect() as agent_conn:
        result = run_investigation(
            agent_conn, provider,
            exception_id=row["exception_id"],
            payment_id=row["payment_id"],
            delta=row["delta"],
            cfg=cfg, as_of=as_of,
            max_tool_calls=config.llm_max_tool_calls,
            rule_flag=row["exception_type"] if row["delta"] == 0 else None,
        )
        persist(agent_conn, result)
        agent_conn.commit()

    # The agent role is read-only on `exceptions`; the case status is written
    # back by the owner. The agent records what it found - it does not close
    # its own ticket.
    with owner_engine().begin() as writer:
        writer.execute(
            text(
                "UPDATE recon.exceptions SET status = :s, evidence_score = :e"
                " WHERE exception_id = :i"
            ),
            {"s": result.final_status, "e": result.evidence_score, "i": exception_id},
        )

    return get_exception(exception_id, conn)
