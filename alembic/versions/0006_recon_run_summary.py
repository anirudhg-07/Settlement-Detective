"""Let a detected exception be unclassified, and record run-level totals.

Phase 5 detects discrepancies arithmetically; Phase 6 gives them a type. In
between, an exception genuinely has no type yet - NULL says that honestly,
where picking a placeholder from the taxonomy would put a false label in the
audit trail.

The run totals are denormalised deliberately: the Command Centre screen reads
them directly rather than aggregating 10,000 result rows on every page load.

Revision ID: 0006
Revises: 0005
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

COLUMNS = (
    ("matched_count", sa.Integer()),
    ("pending_count", sa.Integer()),
    ("exception_count", sa.Integer()),
    ("batches_checked", sa.Integer()),
    ("batches_out_of_balance", sa.Integer()),
)


def upgrade() -> None:
    op.alter_column(
        "exceptions", "exception_type", nullable=True, schema="recon"
    )
    for name, type_ in COLUMNS:
        op.add_column(
            "recon_runs",
            sa.Column(name, type_, nullable=False, server_default="0"),
            schema="recon",
        )


def downgrade() -> None:
    for name, _ in COLUMNS:
        op.drop_column("recon_runs", name, schema="recon")
    op.alter_column("exceptions", "exception_type", nullable=False, schema="recon")
