"""Request-scoped database access, one connection per role.

The API is a service layer, not a user of the agent's least-privilege role, so
reads of `ops` and `recon` go through the owner connection. Ground truth is
different: it is reachable only through `eval_connection`, which exists solely
so the analytics endpoint can report aggregate accuracy.

No endpoint returns per-case ground truth. Aggregates are a legitimate product
surface - an operations team needs to know how well detection is working. A
per-payment answer key is not, and exposing one over HTTP would make every
accuracy figure this project reports meaningless.
"""

from __future__ import annotations

from datetime import date
from typing import Iterator

from fastapi import Depends, HTTPException
from sqlalchemy import Connection, text

from backend.config import FinancialConfig, Settings, get_settings


def settings() -> Settings:
    return get_settings()


def financial_config(config: Settings = Depends(settings)) -> FinancialConfig:
    return config.financial()


def as_of_date(config: Settings = Depends(settings)) -> date:
    return config.as_of_date


def connection() -> Iterator[Connection]:
    """Read connection over `ops` and `recon`."""
    from backend.db.session import owner_engine

    with owner_engine().connect() as conn:
        yield conn


def eval_connection() -> Iterator[Connection]:
    """The only path to ground truth, and only for aggregates."""
    from backend.db.session import eval_engine

    with eval_engine().connect() as conn:
        yield conn


def latest_run_id(conn: Connection = Depends(connection)) -> str:
    run_id = conn.execute(
        text("SELECT run_id FROM recon.recon_runs ORDER BY started_at DESC LIMIT 1")
    ).scalar()
    if not run_id:
        raise HTTPException(
            status_code=404,
            detail="no reconciliation run found - run scripts/reconcile.py first",
        )
    return run_id
