"""Enforce `sum(refunds) <= payment.amount` in the database.

Phase 2 left this as a service-layer validator, which is a real gap: a
cross-row invariant that only application code enforces is one careless
`INSERT` away from producing an over-refunded payment that then reconciles as a
financial discrepancy rather than the data bug it is.

`FOR UPDATE` on the payment row serialises concurrent refund inserts for the
same payment, so two simultaneous refunds cannot each pass the check.

Revision ID: 0004
Revises: 0003
"""
from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

FUNCTION = """
CREATE OR REPLACE FUNCTION ops.check_refund_total() RETURNS trigger AS $$
DECLARE
    pay_amount    BIGINT;
    refund_total  BIGINT;
BEGIN
    -- Lock the payment row so concurrent refunds serialise on it.
    SELECT amount INTO pay_amount
    FROM ops.payments
    WHERE payment_id = NEW.payment_id
    FOR UPDATE;

    SELECT COALESCE(SUM(amount), 0) INTO refund_total
    FROM ops.refunds
    WHERE payment_id = NEW.payment_id
      AND status <> 'failed'
      AND refund_id <> NEW.refund_id;

    IF NEW.status <> 'failed' THEN
        refund_total := refund_total + NEW.amount;
    END IF;

    IF refund_total > pay_amount THEN
        RAISE EXCEPTION
            'ck_refunds_total_within_payment: refunds total % paise exceed payment % of % paise',
            refund_total, NEW.payment_id, pay_amount
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

TRIGGER = """
CREATE TRIGGER trg_refunds_total_within_payment
BEFORE INSERT OR UPDATE ON ops.refunds
FOR EACH ROW EXECUTE FUNCTION ops.check_refund_total();
"""


def upgrade() -> None:
    op.execute(FUNCTION)
    op.execute(TRIGGER)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_refunds_total_within_payment ON ops.refunds")
    op.execute("DROP FUNCTION IF EXISTS ops.check_refund_total()")
