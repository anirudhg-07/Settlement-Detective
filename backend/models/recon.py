"""``recon`` - reconciliation results, exceptions, and the investigation audit trail.

``investigation_steps`` is append-only by database grant, not by convention:
the roles that write it are given INSERT and no UPDATE or DELETE, so an audit
trail cannot be quietly rewritten after the fact (Phase 1 spec, section 24).
"""

from __future__ import annotations

from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.enums import (
    DetectedBy,
    EvidenceRole,
    ExceptionStatus,
    ExceptionType,
    InvestigationMode,
    ReconStatus,
)
from backend.models.base import (
    SCHEMA_OPS,
    SCHEMA_RECON,
    Base,
    enum_col,
    id_col,
    money_col,
    ts_col,
)


class ReconRun(Base):
    """One sweep of the reconciliation engine over the dataset."""

    __tablename__ = "recon_runs"
    __table_args__ = {"schema": SCHEMA_RECON}

    run_id: Mapped[str] = id_col(primary_key=True)
    #: Frozen clock for the run. Timing decisions must never read the wall
    #: clock, or the same dataset reconciles differently tomorrow.
    as_of_date: Mapped[date] = mapped_column(sa.Date, nullable=False)
    tolerance_paise: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    #: Full FinancialConfig.snapshot() - a historical result must always be
    #: explainable by the parameters it was produced under.
    config_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    started_at: Mapped[datetime] = ts_col(nullable=False)
    completed_at: Mapped[datetime | None] = ts_col(nullable=True)
    records_processed: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0
    )
    #: Denormalised run totals - the Command Centre reads these directly
    #: rather than aggregating every result row on each page load.
    matched_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0, server_default="0")
    pending_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0, server_default="0")
    exception_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0, server_default="0")
    batches_checked: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0, server_default="0")
    batches_out_of_balance: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0, server_default="0")


class ReconResult(Base):
    """Per-payment reconciliation outcome for a run."""

    __tablename__ = "recon_results"
    __table_args__ = (
        sa.UniqueConstraint("run_id", "payment_id", name="uq_recon_results_run_id"),
        sa.Index("ix_recon_results_status", "status"),
        {"schema": SCHEMA_RECON},
    )

    result_id: Mapped[str] = id_col(primary_key=True)
    run_id: Mapped[str] = id_col(
        sa.ForeignKey(f"{SCHEMA_RECON}.recon_runs.run_id"), nullable=False
    )
    payment_id: Mapped[str] = id_col(
        sa.ForeignKey(f"{SCHEMA_OPS}.payments.payment_id"), nullable=False
    )
    expected_net: Mapped[int] = money_col(nullable=False)
    actual_net: Mapped[int] = money_col(nullable=False)
    delta: Mapped[int] = money_col(nullable=False)
    status: Mapped[str] = mapped_column(
        enum_col(ReconStatus, "recon_status"), nullable=False
    )
    #: DataCondition flags raised while computing the expectation.
    flags: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    computed_at: Mapped[datetime] = ts_col(nullable=False)


class Exception_(Base):
    """A detected discrepancy awaiting - or having received - investigation."""

    __tablename__ = "exceptions"
    __table_args__ = (
        sa.CheckConstraint(
            "evidence_score IS NULL OR (evidence_score >= 0 AND evidence_score <= 100)",
            name="evidence_score_range",
        ),
        sa.Index("ix_exceptions_status", "status"),
        sa.Index("ix_exceptions_exception_type", "exception_type"),
        {"schema": SCHEMA_RECON},
    )

    exception_id: Mapped[str] = id_col(primary_key=True)
    run_id: Mapped[str] = id_col(
        sa.ForeignKey(f"{SCHEMA_RECON}.recon_runs.run_id"), nullable=False
    )
    payment_id: Mapped[str] = id_col(
        sa.ForeignKey(f"{SCHEMA_OPS}.payments.payment_id"), nullable=False
    )
    #: NULL until Phase 6 classifies it. A detected-but-unclassified exception
    #: genuinely has no type, and a placeholder from the taxonomy would put a
    #: false label into the audit trail.
    exception_type: Mapped[str | None] = mapped_column(
        enum_col(ExceptionType, "exception_type"), nullable=True
    )
    expected_net: Mapped[int] = money_col(nullable=False)
    actual_net: Mapped[int] = money_col(nullable=False)
    delta: Mapped[int] = money_col(nullable=False)
    detected_by: Mapped[str] = mapped_column(
        enum_col(DetectedBy, "detected_by"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        enum_col(ExceptionStatus, "exception_status"),
        nullable=False,
        default=ExceptionStatus.OPEN.value,
    )
    evidence_score: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    created_at: Mapped[datetime] = ts_col(nullable=False)

    investigations: Mapped[list["Investigation"]] = relationship(
        back_populates="exception"
    )


class Investigation(Base):
    """One attempt - baseline or AI - to explain one exception.

    Both modes write here, so the evaluation can compare them on identical
    exceptions rather than on separately-collected samples.
    """

    __tablename__ = "investigations"
    __table_args__ = (
        sa.Index("ix_investigations_exception_id", "exception_id"),
        {"schema": SCHEMA_RECON},
    )

    investigation_id: Mapped[str] = id_col(primary_key=True)
    exception_id: Mapped[str] = id_col(
        sa.ForeignKey(f"{SCHEMA_RECON}.exceptions.exception_id"), nullable=False
    )
    mode: Mapped[str] = mapped_column(
        enum_col(InvestigationMode, "investigation_mode"), nullable=False
    )
    started_at: Mapped[datetime] = ts_col(nullable=False)
    completed_at: Mapped[datetime | None] = ts_col(nullable=True)
    llm_model: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
    #: What the model said about its own certainty. Recorded but never trusted -
    #: the decision is driven by `evidence_score`, which code computes.
    reasoning_confidence: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    evidence_score: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    decision: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    final_status: Mapped[str | None] = mapped_column(
        enum_col(ExceptionStatus, "final_exception_status"), nullable=True
    )
    #: The part of DELTA that no evidence accounted for. Must be 0 (within
    #: tolerance) for a RESOLVED outcome - this is what makes "I don't know"
    #: a computed result rather than a stylistic choice.
    unexplained_amount: Mapped[int | None] = money_col(nullable=True)
    tool_call_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    tokens_used: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)

    exception: Mapped["Exception_"] = relationship(back_populates="investigations")
    steps: Mapped[list["InvestigationStep"]] = relationship(
        back_populates="investigation"
    )


class InvestigationStep(Base):
    """One step of the agent loop: a tool call and what it returned.

    Append-only. See the grants in migration 0003.
    """

    __tablename__ = "investigation_steps"
    __table_args__ = (
        sa.UniqueConstraint(
            "investigation_id", "seq", name="uq_investigation_steps_investigation_id"
        ),
        {"schema": SCHEMA_RECON},
    )

    step_id: Mapped[str] = id_col(primary_key=True)
    investigation_id: Mapped[str] = id_col(
        sa.ForeignKey(f"{SCHEMA_RECON}.investigations.investigation_id"), nullable=False
    )
    seq: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    step_type: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    tool_name: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    tool_args: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    tool_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    observation: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    created_at: Mapped[datetime] = ts_col(nullable=False)

    investigation: Mapped["Investigation"] = relationship(back_populates="steps")


class Evidence(Base):
    """A concrete record cited in support of - or against - a conclusion.

    ``amount_contribution`` is the signed paise this record explains. The
    evidence rows for a resolved exception must sum to DELTA; whatever is left
    over is the unexplained amount.
    """

    __tablename__ = "evidence"
    __table_args__ = (
        sa.Index("ix_evidence_investigation_id", "investigation_id"),
        {"schema": SCHEMA_RECON},
    )

    evidence_id: Mapped[str] = id_col(primary_key=True)
    investigation_id: Mapped[str] = id_col(
        sa.ForeignKey(f"{SCHEMA_RECON}.investigations.investigation_id"), nullable=False
    )
    record_type: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    record_id: Mapped[str] = id_col(nullable=False)
    role: Mapped[str] = mapped_column(
        enum_col(EvidenceRole, "evidence_role"), nullable=False
    )
    amount_contribution: Mapped[int | None] = money_col(nullable=True)
    note: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
