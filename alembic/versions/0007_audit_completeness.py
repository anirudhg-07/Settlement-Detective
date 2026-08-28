"""Make an investigation reconstructable, and tampering detectable.

Three additions:

* `investigations.score_factors` - the named deductions behind the evidence
  score. Storing only the number leaves an auditor unable to answer "why 41?"
  from the record itself.
* `investigations.records_examined` - every financial record the investigation
  actually looked at, per spec section 24.
* `investigation_steps.prev_hash` / `content_hash` - a hash chain over the
  trail. The agent role already cannot UPDATE or DELETE these rows; the chain
  extends that guarantee to everyone else, because a row edited by any means
  no longer matches the hash computed over it.

Revision ID: 0007
Revises: 0006
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("investigations", sa.Column("score_factors", JSONB(), nullable=True),
                  schema="recon")
    op.add_column("investigations", sa.Column("records_examined", JSONB(), nullable=True),
                  schema="recon")
    op.add_column("investigation_steps", sa.Column("prev_hash", sa.String(64), nullable=True),
                  schema="recon")
    op.add_column("investigation_steps", sa.Column("content_hash", sa.String(64), nullable=True),
                  schema="recon")


def downgrade() -> None:
    op.drop_column("investigation_steps", "content_hash", schema="recon")
    op.drop_column("investigation_steps", "prev_hash", schema="recon")
    op.drop_column("investigations", "records_examined", schema="recon")
    op.drop_column("investigations", "score_factors", schema="recon")
