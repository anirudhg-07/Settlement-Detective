"""``gt`` - ground truth. Quarantined.

The AI investigation agent must never see these values, or every accuracy
number this project reports is fiction. That is enforced at the database
privilege level in migration 0003: the ``sd_agent`` role holds no grant on this
schema at all, so a leak surfaces as ``permission denied`` at runtime rather
than depending on nobody ever writing a careless join.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import SCHEMA_GT, SCHEMA_OPS, Base, id_col, money_col


class CaseTruth(Base):
    __tablename__ = "case_truth"
    __table_args__ = {"schema": SCHEMA_GT}

    payment_id: Mapped[str] = id_col(
        sa.ForeignKey(f"{SCHEMA_OPS}.payments.payment_id"), primary_key=True
    )
    is_exception: Mapped[bool] = mapped_column(sa.Boolean, nullable=False)
    #: An ExceptionType value, or one of the injected difficulty families
    #: (MULTI_CAUSE / CROSS_ENTITY / TIMING_SHIFTED). Free text so Phase 4 can
    #: add families without a migration.
    reason_code: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    explained_amount: Mapped[int | None] = money_col(nullable=True)
    injection_params: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    notes: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
