"""Engine and session factories, one per database role.

Three roles, three engines, and which one you get is a deliberate choice:

``owner_engine``  migrations, data generation, the reconciliation engine
``agent_engine``  the AI investigation tool layer - cannot read schema ``gt``
``eval_engine``   evaluation scripts only - the sole reader of ground truth
"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.config import get_settings


def _engine(url: str, label: str) -> Engine:
    if not url:
        raise RuntimeError(
            f"no database URL configured for the {label!r} role; "
            "copy .env.example to .env and fill it in"
        )
    return create_engine(url, pool_pre_ping=True, future=True)


@lru_cache(maxsize=1)
def owner_engine() -> Engine:
    return _engine(get_settings().database_url, "owner")


@lru_cache(maxsize=1)
def agent_engine() -> Engine:
    """Least-privilege engine for the investigation agent's tools."""
    return _engine(get_settings().agent_database_url, "agent")


@lru_cache(maxsize=1)
def eval_engine() -> Engine:
    return _engine(get_settings().eval_database_url, "eval")


@lru_cache(maxsize=1)
def _owner_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=owner_engine(), expire_on_commit=False, future=True)


def owner_session() -> Session:
    return _owner_sessionmaker()()
