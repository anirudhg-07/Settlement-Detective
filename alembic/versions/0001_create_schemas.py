"""Create the three schemas and lock down `gt`.

Revision ID: 0001
Revises:
"""
from __future__ import annotations

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMAS = ("ops", "recon", "gt")


def upgrade() -> None:
    for schema in SCHEMAS:
        op.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    # Belt and braces: PUBLIC gets nothing on ground truth, so a role created
    # later cannot inherit access to it by accident.
    op.execute("REVOKE ALL ON SCHEMA gt FROM PUBLIC")


def downgrade() -> None:
    for schema in reversed(SCHEMAS):
        op.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
