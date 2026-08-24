"""Allow a payment to settle across batches, without allowing double-counting.

Phase 2 enforced "one PAYMENT line per payment" with a unique index. Phase 4
showed that to be too strict: genuine partial settlement - the same payment
settling across two batches, with the remainder in a batch still to be
processed - is a real exception type and the index made it unrepresentable.

The replacement is stronger, not weaker. A trigger enforces that the credits
across all of a payment's PAYMENT lines never exceed the payment itself, so
double-counting is still impossible while a legitimate split is allowed.

Revision ID: 0005
Revises: 0004
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

FUNCTION = """
CREATE OR REPLACE FUNCTION ops.check_payment_settled_once() RETURNS trigger AS $$
DECLARE
    pay_amount     BIGINT;
    settled_total  BIGINT;
BEGIN
    IF NEW.item_type <> 'PAYMENT' THEN
        RETURN NEW;
    END IF;

    SELECT amount INTO pay_amount
    FROM ops.payments
    WHERE payment_id = NEW.payment_id
    FOR UPDATE;

    SELECT COALESCE(SUM(credit_amount), 0) INTO settled_total
    FROM ops.settlement_items
    WHERE payment_id = NEW.payment_id
      AND item_type = 'PAYMENT'
      AND item_id <> NEW.item_id;

    settled_total := settled_total + NEW.credit_amount;

    IF settled_total > pay_amount THEN
        RAISE EXCEPTION
            'ck_payment_not_over_settled: settlement lines credit % paise for payment %, which is only % paise',
            settled_total, NEW.payment_id, pay_amount
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

TRIGGER = """
CREATE TRIGGER trg_payment_not_over_settled
BEFORE INSERT OR UPDATE ON ops.settlement_items
FOR EACH ROW EXECUTE FUNCTION ops.check_payment_settled_once();
"""


def upgrade() -> None:
    op.drop_index(
        "uq_settlement_items_payment_once",
        table_name="settlement_items",
        schema="ops",
        postgresql_where=sa.text("item_type = 'PAYMENT'"),
    )
    op.create_index(
        "ix_settlement_items_payment_id", "settlement_items", ["payment_id"], schema="ops"
    )
    op.execute(FUNCTION)
    op.execute(TRIGGER)


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_payment_not_over_settled ON ops.settlement_items"
    )
    op.execute("DROP FUNCTION IF EXISTS ops.check_payment_settled_once()")
    op.drop_index("ix_settlement_items_payment_id", table_name="settlement_items", schema="ops")
    op.create_index(
        "uq_settlement_items_payment_once",
        "settlement_items",
        ["payment_id"],
        unique=True,
        schema="ops",
        postgresql_where=sa.text("item_type = 'PAYMENT'"),
    )
