"""G6 - write-time guards and the database-level safety controls.

The tests marked `db` are the ones that matter most for the product's honesty
claim: they assert that the ground-truth quarantine and the append-only audit
trail are enforced by Postgres, not by developer discipline.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, ProgrammingError

from backend.config import get_settings
from backend.enums import (
    PaymentMethod,
    PaymentStatus,
    RefundStatus,
    SettlementItemType,
    SettlementStatus,
)
from backend.reconciliation.guards import (
    GuardViolation,
    validate_currency,
    validate_non_negative,
    validate_refund_total,
)

NOW = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)


# --- 26. refunds may not exceed the payment -------------------------------
def test_refund_total_within_payment_is_allowed():
    assert (
        validate_refund_total(
            100_000,
            [(40_000, RefundStatus.PROCESSED), (10_000, RefundStatus.CREATED)],
        )
        == 50_000
    )


def test_refund_total_exceeding_payment_is_rejected():
    with pytest.raises(GuardViolation, match="exceed"):
        validate_refund_total(
            100_000,
            [(80_000, RefundStatus.PROCESSED), (30_000, RefundStatus.PROCESSED)],
        )


def test_pending_refunds_count_toward_the_limit():
    """Over-refunding should be impossible to request, not merely to complete."""
    with pytest.raises(GuardViolation):
        validate_refund_total(
            100_000,
            [(60_000, RefundStatus.PROCESSED), (60_000, RefundStatus.CREATED)],
        )


def test_failed_refunds_do_not_count():
    assert (
        validate_refund_total(
            100_000,
            [(80_000, RefundStatus.FAILED), (80_000, RefundStatus.PROCESSED)],
        )
        == 80_000
    )


# --- 27. currency -----------------------------------------------------------
def test_non_inr_currency_is_rejected():
    """A USD row must fail loudly, not be reconciled as if cents were paise."""
    with pytest.raises(GuardViolation, match="unsupported currency"):
        validate_currency("USD")


def test_inr_is_accepted_case_insensitively():
    assert validate_currency("inr") == "INR"


def test_negative_amounts_are_rejected():
    with pytest.raises(GuardViolation):
        validate_non_negative("amount", -1)
    with pytest.raises(GuardViolation):
        validate_non_negative("amount", 1.5)  # not an int
    assert validate_non_negative("amount", 0) == 0


# --------------------------------------------------------------------------
# Database-level controls
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def owner_conn():
    from backend.db.session import owner_engine

    try:
        engine = owner_engine()
        with engine.connect() as conn:
            conn.execute(sa.text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"PostgreSQL unavailable ({exc}); run `docker compose up -d`")
    with engine.connect() as conn:
        yield conn


@pytest.fixture
def seeded(owner_conn):
    """A single payment and settlement, rolled back after the test."""
    tx = owner_conn.begin()
    owner_conn.execute(
        sa.text(
            "INSERT INTO ops.customers VALUES (:cid, 'retail', :now)"
        ),
        {"cid": "cust_t1", "now": NOW},
    )
    owner_conn.execute(
        sa.text(
            "INSERT INTO ops.orders VALUES (:oid, :cid, 100000, 'INR', 'paid', :now)"
        ),
        {"oid": "ord_t1", "cid": "cust_t1", "now": NOW},
    )
    owner_conn.execute(
        sa.text(
            "INSERT INTO ops.payments VALUES "
            "(:pid, :oid, :cid, 100000, 'INR', 'card', 'captured', :now, :now)"
        ),
        {"pid": "pay_t1", "oid": "ord_t1", "cid": "cust_t1", "now": NOW},
    )
    owner_conn.execute(
        sa.text(
            "INSERT INTO ops.settlements VALUES "
            "(:sid, 97640, 'UTR123', 'processed', :now, :now)"
        ),
        {"sid": "setl_t1", "now": NOW},
    )
    yield owner_conn
    tx.rollback()


def _insert_item(conn, item_id, **over):
    row = dict(
        item_id=item_id,
        settlement_id="setl_t1",
        item_type=SettlementItemType.PAYMENT.value,
        payment_id="pay_t1",
        refund_id=None,
        adjustment_id=None,
        credit=100_000,
        fee=2_000,
        tax=360,
        net=97_640,
        now=NOW,
    )
    row.update(over)
    conn.execute(
        sa.text(
            "INSERT INTO ops.settlement_items VALUES "
            "(:item_id, :settlement_id, :item_type, :payment_id, :refund_id, "
            ":adjustment_id, :credit, :fee, :tax, :net, :now)"
        ),
        row,
    )


# --- 28. a payment may be settled only once -------------------------------
@pytest.mark.db
def test_payment_cannot_be_over_settled(seeded):
    """Two full-value PAYMENT lines would double-count the money.

    Migration 0005 moved this from a unique index to a trigger so that genuine
    partial settlement stays representable - but over-settling must still be
    impossible.
    """
    _insert_item(seeded, "si_t1")
    with pytest.raises(Exception, match="ck_payment_not_over_settled"):
        _insert_item(seeded, "si_t2")


@pytest.mark.db
def test_a_payment_may_legitimately_settle_across_two_batches(seeded):
    """Partial settlement: the credits split, and together they never exceed."""
    _insert_item(seeded, "si_p1", credit=60_000, fee=2_000, tax=360, net=57_640)
    _insert_item(seeded, "si_p2", credit=40_000, fee=0, tax=0, net=40_000)
    total = seeded.execute(
        sa.text(
            "SELECT sum(credit_amount) FROM ops.settlement_items "
            "WHERE payment_id = 'pay_t1' AND item_type = 'PAYMENT'"
        )
    ).scalar()
    assert total == 100_000


@pytest.mark.db
def test_settlement_item_must_have_exactly_one_subject(seeded):
    with pytest.raises(IntegrityError, match="exactly_one_subject"):
        _insert_item(seeded, "si_t3", payment_id=None)


@pytest.mark.db
def test_refund_line_may_not_be_a_credit(seeded):
    seeded.execute(
        sa.text(
            "INSERT INTO ops.refunds VALUES "
            "(:rid, :pid, :oid, 40000, 'processed', :now, :now)"
        ),
        {"rid": "rfnd_t1", "pid": "pay_t1", "oid": "ord_t1", "now": NOW},
    )
    with pytest.raises(IntegrityError, match="refund_line_is_a_debit"):
        _insert_item(
            seeded,
            "si_t4",
            item_type=SettlementItemType.REFUND.value,
            payment_id=None,
            refund_id="rfnd_t1",
            credit=0,
            fee=0,
            tax=0,
            net=40_000,  # positive - wrong sign
        )


@pytest.mark.db
def test_non_inr_currency_is_rejected_by_the_database(seeded):
    with pytest.raises(IntegrityError, match="currency_inr_only"):
        seeded.execute(
            sa.text(
                "INSERT INTO ops.orders VALUES "
                "(:oid, :cid, 100000, 'USD', 'paid', :now)"
            ),
            {"oid": "ord_usd", "cid": "cust_t1", "now": NOW},
        )


@pytest.mark.db
def test_negative_payment_amount_is_rejected_by_the_database(seeded):
    with pytest.raises(IntegrityError, match="amount_non_negative"):
        seeded.execute(
            sa.text(
                "INSERT INTO ops.payments VALUES "
                "(:pid, :oid, :cid, -1, 'INR', 'card', 'captured', :now, :now)"
            ),
            {"pid": "pay_neg", "oid": "ord_t1", "cid": "cust_t1", "now": NOW},
        )


# --- 30. the ground-truth quarantine --------------------------------------
@pytest.fixture(scope="module")
def agent_conn():
    from backend.db.session import agent_engine

    try:
        engine = agent_engine()
        with engine.connect() as conn:
            conn.execute(sa.text("SELECT 1"))
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"sd_agent role unavailable ({exc})")
    with engine.connect() as conn:
        yield conn


@pytest.mark.db
def test_agent_role_cannot_read_ground_truth(agent_conn):
    """THE test. If this ever passes silently, every accuracy number is fiction."""
    with pytest.raises(ProgrammingError) as exc:
        agent_conn.execute(sa.text("SELECT * FROM gt.case_truth"))
    assert "permission denied" in str(exc.value).lower()


@pytest.mark.db
def test_agent_role_can_read_operational_records(agent_conn):
    """The quarantine must not have broken the agent's legitimate access."""
    agent_conn.rollback()
    assert agent_conn.execute(sa.text("SELECT count(*) FROM ops.payments")).scalar() >= 0
    assert agent_conn.execute(sa.text("SELECT count(*) FROM recon.exceptions")).scalar() >= 0


@pytest.mark.db
def test_agent_role_cannot_modify_financial_records(agent_conn):
    """The agent is an investigator, never a financial transaction modifier."""
    agent_conn.rollback()
    with pytest.raises(ProgrammingError) as exc:
        agent_conn.execute(sa.text("UPDATE ops.payments SET amount = 1"))
    assert "permission denied" in str(exc.value).lower()


@pytest.mark.db
def test_audit_trail_is_append_only_for_the_agent(agent_conn):
    """Section 24: audit history must not be silently alterable."""
    agent_conn.rollback()
    for statement in (
        "UPDATE recon.investigation_steps SET observation = 'x'",
        "DELETE FROM recon.investigation_steps",
    ):
        with pytest.raises(ProgrammingError) as exc:
            agent_conn.execute(sa.text(statement))
        assert "permission denied" in str(exc.value).lower()
        agent_conn.rollback()


@pytest.mark.db
def test_eval_role_can_read_ground_truth():
    """Evaluation needs the answers; only evaluation gets them."""
    from backend.db.session import eval_engine

    if not get_settings().eval_database_url:
        pytest.skip("no eval URL configured")
    with eval_engine().connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM gt.case_truth")).scalar() >= 0


# --- the refund-total trigger (migration 0004) ----------------------------
@pytest.mark.db
def test_refund_total_trigger_rejects_over_refunding(seeded):
    """A cross-row invariant enforced by the database, not just by app code."""
    seeded.execute(
        sa.text(
            "INSERT INTO ops.refunds VALUES "
            "(:rid, :pid, :oid, 80000, 'processed', :now, :now)"
        ),
        {"rid": "rfnd_ok", "pid": "pay_t1", "oid": "ord_t1", "now": NOW},
    )
    with pytest.raises(Exception, match="ck_refunds_total_within_payment"):
        seeded.execute(
            sa.text(
                "INSERT INTO ops.refunds VALUES "
                "(:rid, :pid, :oid, 30000, 'processed', :now, :now)"
            ),
            {"rid": "rfnd_over", "pid": "pay_t1", "oid": "ord_t1", "now": NOW},
        )


@pytest.mark.db
def test_refund_total_trigger_allows_refunds_up_to_the_payment(seeded):
    for rid, amount in (("rfnd_a", 60_000), ("rfnd_b", 40_000)):
        seeded.execute(
            sa.text(
                "INSERT INTO ops.refunds VALUES "
                "(:rid, :pid, :oid, :amt, 'processed', :now, :now)"
            ),
            {"rid": rid, "pid": "pay_t1", "oid": "ord_t1", "amt": amount, "now": NOW},
        )
    total = seeded.execute(
        sa.text("SELECT sum(amount) FROM ops.refunds WHERE payment_id = 'pay_t1'")
    ).scalar()
    assert total == 100_000


@pytest.mark.db
def test_failed_refunds_do_not_count_toward_the_trigger_limit(seeded):
    for rid, amount, status in (
        ("rfnd_f", 100_000, "failed"),
        ("rfnd_g", 100_000, "processed"),
    ):
        seeded.execute(
            sa.text(
                "INSERT INTO ops.refunds VALUES "
                "(:rid, :pid, :oid, :amt, :st, :now, :now)"
            ),
            {
                "rid": rid, "pid": "pay_t1", "oid": "ord_t1",
                "amt": amount, "st": status, "now": NOW,
            },
        )
