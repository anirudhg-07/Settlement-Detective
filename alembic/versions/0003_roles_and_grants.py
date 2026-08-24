"""Least-privilege roles: `sd_agent` and `sd_eval`.

This migration is a security control, not housekeeping.

`sd_agent` is the role the AI investigation tool layer connects as. It holds
**no grant of any kind on schema `gt`**, so if a tool, a join, or a future
refactor ever tries to read ground truth, Postgres raises `permission denied`
instead of quietly handing the model the answer it is being evaluated on.

It also gets INSERT but not UPDATE or DELETE on `recon.investigation_steps`,
which makes the audit trail append-only at the database level rather than by
convention (Phase 1 spec, section 24).

Revision ID: 0003
Revises: 0002
"""
from __future__ import annotations

from alembic import op

from backend.config import get_settings

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

AGENT_ROLE = "sd_agent"
EVAL_ROLE = "sd_eval"

#: Tables the agent may append investigation output to.
AGENT_WRITABLE = (
    "recon.investigations",
    "recon.investigation_steps",
    "recon.evidence",
)


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _ensure_role(role: str, password: str) -> None:
    if not password:
        raise RuntimeError(
            f"no password configured for role {role!r}; "
            f"set SD_{role.split('_')[1].upper()}_PASSWORD in .env"
        )
    literal = _quote_literal(password)
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                CREATE ROLE {role} LOGIN PASSWORD {literal};
            ELSE
                ALTER ROLE {role} WITH LOGIN PASSWORD {literal};
            END IF;
        END
        $$;
        """
    )
    # Neither role may create objects; they are readers, not owners.
    op.execute(f"ALTER ROLE {role} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT")


def upgrade() -> None:
    settings = get_settings()
    _ensure_role(AGENT_ROLE, settings.sd_agent_password)
    _ensure_role(EVAL_ROLE, settings.sd_eval_password)

    # ---- sd_agent -------------------------------------------------------
    # Read the operational and reconciliation record; write nothing to `ops`.
    op.execute(f"GRANT USAGE ON SCHEMA ops, recon TO {AGENT_ROLE}")
    op.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA ops TO {AGENT_ROLE}")
    op.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA recon TO {AGENT_ROLE}")
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA ops GRANT SELECT ON TABLES TO {AGENT_ROLE}"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA recon GRANT SELECT ON TABLES TO {AGENT_ROLE}"
    )
    # Append-only investigation output: INSERT, never UPDATE or DELETE.
    for table in AGENT_WRITABLE:
        op.execute(f"GRANT INSERT ON {table} TO {AGENT_ROLE}")
        op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON {table} FROM {AGENT_ROLE}")

    # THE quarantine. Stated explicitly so its removal is a visible diff.
    op.execute(f"REVOKE ALL ON SCHEMA gt FROM {AGENT_ROLE}")
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA gt FROM {AGENT_ROLE}")

    # ---- sd_eval --------------------------------------------------------
    # The only role permitted to read ground truth. Never used by the agent.
    op.execute(f"GRANT USAGE ON SCHEMA ops, recon, gt TO {EVAL_ROLE}")
    for schema in ("ops", "recon", "gt"):
        op.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO {EVAL_ROLE}")
        op.execute(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} "
            f"GRANT SELECT ON TABLES TO {EVAL_ROLE}"
        )


def downgrade() -> None:
    for role in (AGENT_ROLE, EVAL_ROLE):
        for schema in ("ops", "recon", "gt"):
            op.execute(
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} "
                f"REVOKE SELECT ON TABLES FROM {role}"
            )
            op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA {schema} FROM {role}")
            op.execute(f"REVOKE ALL ON SCHEMA {schema} FROM {role}")
        op.execute(f"DROP ROLE IF EXISTS {role}")
