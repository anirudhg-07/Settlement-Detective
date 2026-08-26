"""G12 - evidence verification.

Phase 7 checked that a cited record exists. That is not enough: a model can
cite a perfectly real Rs20 fee row and claim it accounts for Rs500. The
residual closes, the case resolves, and the number came from nowhere.

These tests hold the line that makes the whole product defensible:

    the model reasons about WHICH records matter
    the code decides WHAT those records contain
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from backend.agents.evidence import (
    RECORD_TABLES,
    build_package,
    supported_contributions,
    verify_citations,
)
from backend.config import FinancialConfig, get_settings
from backend.enums import ExceptionType


@pytest.fixture(scope="module")
def cfg_mod() -> FinancialConfig:
    return get_settings().financial()


@pytest.fixture
def conn():
    from backend.db.session import agent_engine

    try:
        engine = agent_engine()
        with engine.connect() as probe:
            probe.execute(sa.text("SELECT 1"))
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"PostgreSQL/sd_agent unavailable ({exc})")
    with engine.connect() as connection:
        tx = connection.begin()
        try:
            if connection.execute(sa.text("SELECT count(*) FROM ops.payments")).scalar() == 0:
                pytest.skip("no dataset loaded")
            yield connection
        finally:
            tx.rollback()


def _case(conn, exception_type: str) -> dict:
    row = conn.execute(
        sa.text(
            "SELECT payment_id, delta FROM recon.exceptions"
            " WHERE exception_type = :t AND delta <> 0 LIMIT 1"
        ),
        {"t": exception_type},
    ).mappings().one_or_none()
    if row is None:
        pytest.skip(f"no {exception_type} exception in this dataset")
    return dict(row)


def cite(conn, cfg, case, record_type, record_id, amount, cause=None):
    return verify_citations(
        conn,
        [{"record_type": record_type, "record_id": record_id,
          "amount_paise": amount, "note": "test"}],
        payment_id=case["payment_id"],
        delta=case["delta"],
        cause_type=cause or ExceptionType.FEE_MISMATCH.value,
        tolerance=cfg.tolerance_paise,
    )[0]


# --------------------------------------------------------------------------
# The guard this phase exists for
# --------------------------------------------------------------------------


@pytest.mark.db
def test_a_real_record_cannot_be_cited_for_an_invented_amount(conn, cfg_mod):
    """The Phase 8 hole: existence was checked, the amount was not.

    A genuine fee row assigned a number it never contained would close the
    residual and manufacture a resolution out of nothing.
    """
    case = _case(conn, "FEE_MISMATCH")
    fee_id = conn.execute(
        sa.text("SELECT fee_id FROM ops.fees WHERE payment_id = :p"),
        {"p": case["payment_id"]},
    ).scalar()

    honest = cite(conn, cfg_mod, case, "fee", fee_id, case["delta"])
    assert honest.verified, honest.reason

    invented = cite(conn, cfg_mod, case, "fee", fee_id, case["delta"] - 999_99)
    assert not invented.verified
    assert "can only account for" in invented.reason
    assert invented.counts == 0


@pytest.mark.db
def test_the_rejection_says_what_the_record_could_have_supported(conn, cfg_mod):
    """A reviewer must be able to see why a claim failed, not just that it did."""
    case = _case(conn, "FEE_MISMATCH")
    fee_id = conn.execute(
        sa.text("SELECT fee_id FROM ops.fees WHERE payment_id = :p"),
        {"p": case["payment_id"]},
    ).scalar()
    bad = cite(conn, cfg_mod, case, "fee", fee_id, 12_345_678)
    assert bad.supported
    assert "₹" in bad.reason


# --------------------------------------------------------------------------
# What each record type can support
# --------------------------------------------------------------------------


@pytest.mark.db
def test_a_fee_record_supports_the_gap_between_recorded_and_deducted(conn):
    case = _case(conn, "FEE_MISMATCH")
    fee_id = conn.execute(
        sa.text("SELECT fee_id FROM ops.fees WHERE payment_id = :p"),
        {"p": case["payment_id"]},
    ).scalar()
    supported, snapshot = supported_contributions(
        conn, "fee", fee_id, case["payment_id"]
    )
    assert snapshot["deducted_fee"] != snapshot["recorded_fee"]
    assert case["delta"] in supported


@pytest.mark.db
def test_a_refund_record_supports_its_own_amount(conn):
    case = _case(conn, "MISSING_REFUND")
    refund = conn.execute(
        sa.text(
            "SELECT refund_id, amount FROM ops.refunds"
            " WHERE payment_id = :p AND status = 'processed' LIMIT 1"
        ),
        {"p": case["payment_id"]},
    ).mappings().one()
    supported, snapshot = supported_contributions(
        conn, "refund", refund["refund_id"], case["payment_id"]
    )
    assert refund["amount"] in supported
    assert snapshot["recorded_amount"] == refund["amount"]
    assert snapshot["actually_debited"] == 0


@pytest.mark.db
def test_an_adjustment_supports_its_signed_amount(conn):
    row = conn.execute(
        sa.text(
            "SELECT adjustment_id, amount, payment_id FROM ops.adjustments"
            " WHERE payment_id IS NOT NULL LIMIT 1"
        )
    ).mappings().one()
    supported, snapshot = supported_contributions(
        conn, "adjustment", row["adjustment_id"], row["payment_id"]
    )
    assert row["amount"] in supported
    assert snapshot["amount"] == row["amount"]


@pytest.mark.db
def test_amounts_are_integers_not_decimals(conn):
    """Postgres returns NUMERIC for SUM over BIGINT. Money is integer paise
    everywhere, so it is cast back at the query boundary rather than allowed
    to leak inward."""
    case = _case(conn, "FEE_MISMATCH")
    fee_id = conn.execute(
        sa.text("SELECT fee_id FROM ops.fees WHERE payment_id = :p"),
        {"p": case["payment_id"]},
    ).scalar()
    supported, snapshot = supported_contributions(
        conn, "fee", fee_id, case["payment_id"]
    )
    assert all(isinstance(a, int) and not isinstance(a, bool) for a in supported)
    assert all(isinstance(v, int) for v in snapshot.values())


@pytest.mark.db
def test_a_record_type_that_carries_no_amount_supports_nothing(conn, cfg_mod):
    case = _case(conn, "FEE_MISMATCH")
    order_id = conn.execute(
        sa.text("SELECT order_id FROM ops.payments WHERE payment_id = :p"),
        {"p": case["payment_id"]},
    ).scalar()
    citation = cite(conn, cfg_mod, case, "order", order_id, case["delta"])
    assert not citation.verified
    assert "carries no amount" in citation.reason


# --------------------------------------------------------------------------
# Malformed and hostile citations
# --------------------------------------------------------------------------


@pytest.mark.db
def test_hallucinated_records_and_bad_shapes_are_all_rejected(conn, cfg_mod):
    case = _case(conn, "FEE_MISMATCH")
    checks = [
        ("refund", "rfnd_NEVEREXISTED", case["delta"], "no such record"),
        ("wormhole", "who_knows", case["delta"], "unknown record type"),
        ("fee", "fee_aaaaaaaaaaa", "lots", "must be an integer"),
    ]
    for record_type, record_id, amount, expected in checks:
        citation = verify_citations(
            conn,
            [{"record_type": record_type, "record_id": record_id,
              "amount_paise": amount, "note": ""}],
            payment_id=case["payment_id"], delta=case["delta"],
            cause_type=ExceptionType.FEE_MISMATCH.value,
            tolerance=cfg_mod.tolerance_paise,
        )[0]
        assert not citation.verified
        assert expected in citation.reason


@pytest.mark.db
def test_every_citable_type_maps_to_a_real_table(conn):
    for record_type, (table, column) in RECORD_TABLES.items():
        conn.execute(sa.text(f"SELECT {column} FROM {table} LIMIT 1"))


# --------------------------------------------------------------------------
# The package
# --------------------------------------------------------------------------


@pytest.mark.db
def test_the_package_reports_only_what_survived_verification(conn, cfg_mod):
    case = _case(conn, "FEE_MISMATCH")
    fee_id = conn.execute(
        sa.text("SELECT fee_id FROM ops.fees WHERE payment_id = :p"),
        {"p": case["payment_id"]},
    ).scalar()
    citations = verify_citations(
        conn,
        [
            {"record_type": "fee", "record_id": fee_id,
             "amount_paise": case["delta"], "note": "real"},
            {"record_type": "refund", "record_id": "rfnd_MADEUP0001",
             "amount_paise": 500_00, "note": "invented"},
        ],
        payment_id=case["payment_id"], delta=case["delta"],
        cause_type=ExceptionType.FEE_MISMATCH.value,
        tolerance=cfg_mod.tolerance_paise,
    )
    package = build_package(
        citations, payment_id=case["payment_id"], delta=case["delta"],
        cause_type=ExceptionType.FEE_MISMATCH.value,
    )
    assert len(package.verified) == 1
    assert len(package.rejected) == 1
    # The invented Rs500 contributes nothing, so it cannot close the residual.
    assert package.explained == case["delta"]
    assert package.unexplained == 0


@pytest.mark.db
def test_the_package_writes_out_the_arithmetic(conn, cfg_mod):
    """Screen 4 renders these lines; they must add up on the page."""
    case = _case(conn, "FEE_MISMATCH")
    fee_id = conn.execute(
        sa.text("SELECT fee_id FROM ops.fees WHERE payment_id = :p"),
        {"p": case["payment_id"]},
    ).scalar()
    citations = verify_citations(
        conn,
        [{"record_type": "fee", "record_id": fee_id,
          "amount_paise": case["delta"], "note": "over-deducted"}],
        payment_id=case["payment_id"], delta=case["delta"],
        cause_type=ExceptionType.FEE_MISMATCH.value,
        tolerance=cfg_mod.tolerance_paise,
    )
    package = build_package(
        citations, payment_id=case["payment_id"], delta=case["delta"],
        cause_type=ExceptionType.FEE_MISMATCH.value,
    )
    lines = package.calculation_lines()
    assert "discrepancy to explain" in lines[0]
    assert fee_id in lines[1]
    assert "unexplained remainder" in lines[-1]


@pytest.mark.db
def test_an_empty_package_explains_nothing(conn):
    package = build_package([], payment_id="pay_x", delta=-50_000,
                            cause_type=ExceptionType.UNKNOWN_DISCREPANCY.value)
    assert package.explained == 0
    assert package.unexplained == -50_000


# --------------------------------------------------------------------------
# Exceptions with nothing to apportion
# --------------------------------------------------------------------------


@pytest.mark.db
def test_a_zero_delta_case_can_be_corroborated_rather_than_accounted_for(conn, cfg_mod):
    """A duplicate charge reconciles perfectly - there is no money to divide up.

    Evidence there corroborates the finding instead of accounting for a gap, so
    a citation of zero against a real record stands. Without this, every
    rule-detected exception escalates however well the agent reasoned.
    """
    row = conn.execute(
        sa.text(
            "SELECT payment_id FROM recon.exceptions"
            " WHERE exception_type = 'DUPLICATE_PAYMENT' AND delta = 0 LIMIT 1"
        )
    ).mappings().one_or_none()
    if row is None:
        pytest.skip("no duplicate-payment exception in this dataset")

    sibling = conn.execute(
        sa.text(
            "SELECT p2.payment_id FROM ops.payments p1"
            "  JOIN ops.payments p2 ON p2.order_id = p1.order_id"
            "                      AND p2.payment_id <> p1.payment_id"
            " WHERE p1.payment_id = :p LIMIT 1"
        ),
        {"p": row["payment_id"]},
    ).scalar()

    citations = verify_citations(
        conn,
        [
            {"record_type": "payment", "record_id": sibling,
             "amount_paise": 0, "note": "the first charge"},
            {"record_type": "payment", "record_id": row["payment_id"],
             "amount_paise": 0, "note": "the duplicate"},
        ],
        payment_id=row["payment_id"], delta=0,
        cause_type=ExceptionType.DUPLICATE_PAYMENT.value,
        tolerance=cfg_mod.tolerance_paise,
    )
    assert all(c.verified for c in citations)
    assert all(c.snapshot.get("corroborating") for c in citations)


@pytest.mark.db
def test_corroboration_cannot_close_a_real_discrepancy(conn, cfg_mod):
    """The zero-delta allowance must not become a way to wave money through."""
    case = _case(conn, "FEE_MISMATCH")
    citation = cite(conn, cfg_mod, case, "payment", case["payment_id"], 0)
    assert not citation.verified
    assert "cannot be the evidence for its own" in citation.reason
