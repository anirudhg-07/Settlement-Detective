"""Declarative base, shared column types, and schema names."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, mapped_column

SCHEMA_OPS = "ops"
SCHEMA_RECON = "recon"
SCHEMA_GT = "gt"
ALL_SCHEMAS = (SCHEMA_OPS, SCHEMA_RECON, SCHEMA_GT)

# Deterministic constraint names so Alembic autogenerate produces stable diffs.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = sa.MetaData(naming_convention=NAMING_CONVENTION)


def enum_col(enum_cls: type, name: str) -> sa.Enum:
    """A controlled vocabulary stored as VARCHAR + CHECK.

    ``values_callable`` matters: without it SQLAlchemy persists the enum
    *member name* (``CARD``) rather than its value (``card``), silently
    diverging the database from every fixture and CSV in the project.
    """
    return sa.Enum(
        enum_cls,
        name=name,
        native_enum=False,
        length=32,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda e: [m.value for m in e],
    )


def money_col(*args, **kwargs) -> sa.orm.Mapped[int]:
    """A monetary column: BIGINT paise. Never NUMERIC, never DOUBLE PRECISION."""
    return mapped_column(sa.BigInteger, *args, **kwargs)


def ts_col(*args, **kwargs) -> sa.orm.Mapped[datetime]:
    return mapped_column(sa.TIMESTAMP(timezone=True), *args, **kwargs)


def id_col(*args, **kwargs) -> sa.orm.Mapped[str]:
    """Gateway-style opaque identifier, e.g. ``pay_MkT3xQ9``."""
    return mapped_column(sa.String(64), *args, **kwargs)
