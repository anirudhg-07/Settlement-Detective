"""G15 - Razorpay Test Mode integration.

No live calls here: the fixtures are Razorpay's own documented example rows, so
the suite runs offline and deterministically. The live endpoints are exercised
by `scripts/razorpay_sync.py`.

The test that matters most is the cross-validation. Razorpay states the net it
credited; our financial model, run over the gross and the deductions, must land
on the same number. If it does not, the model is wrong about the real world and
every figure this project reports is built on sand.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from backend.razorpay.client import (
    BASE_URL,
    RECON_TYPES,
    SETTLEMENT_STATUSES,
    RazorpayClient,
    RazorpayCredentials,
    RazorpayError,
)
from backend.razorpay.mapping import (
    MappingError,
    check_arithmetic,
    map_recon_row,
    map_settlement,
)

#: Verbatim from https://razorpay.com/docs/api/settlements/fetch-recon/
DOCUMENTED_PAYMENT_ROW = {
    "entity_id": "pay_DEXrnipqTmWVGE",
    "type": "payment",
    "debit": 0,
    "credit": 97100,
    "amount": 100000,
    "currency": "INR",
    "fee": 2900,
    "tax": 0,
    "on_hold": False,
    "settled": True,
    "created_at": 1567692556,
    "settled_at": 1568176960,
    "settlement_id": "setl_DGlQ1Rj8os78Ec",
    "settlement_utr": "1568176960vxp0rj",
    "order_id": "order_DEXrnRiR3SNDHA",
    "method": "card",
}

DOCUMENTED_SETTLEMENT = {
    "id": "setl_7IZKKI4Pnt2kEe",
    "entity": "settlement",
    "amount": 97100,
    "status": "processed",
    "fees": 2900,
    "tax": 0,
    "utr": "1568176960vxp0rj",
    "created_at": 1568176960,
}


# --------------------------------------------------------------------------
# The cross-validation
# --------------------------------------------------------------------------


def test_our_arithmetic_reproduces_razorpays_credited_figure():
    """Their number, our formula, same answer."""
    mapped = map_recon_row(DOCUMENTED_PAYMENT_ROW)
    ok, detail = check_arithmetic(DOCUMENTED_PAYMENT_ROW, mapped)
    assert ok, detail
    assert mapped.net_amount == DOCUMENTED_PAYMENT_ROW["credit"] == 97_100


def test_credit_is_the_net_not_the_gross():
    """The bug this phase caught by reading the docs instead of assuming.

    `credit` is already `amount - fee - tax`. Treating it as gross and
    deducting again takes the fee off twice on every settled payment.
    """
    mapped = map_recon_row(DOCUMENTED_PAYMENT_ROW)
    assert mapped.credit == DOCUMENTED_PAYMENT_ROW["amount"] == 100_000
    assert mapped.net_amount == 97_100
    double_counted = 97_100 - 2_900
    assert mapped.net_amount != double_counted


def test_a_mismatch_would_be_reported_not_swallowed():
    tampered = {**DOCUMENTED_PAYMENT_ROW, "credit": 90_000}
    ok, detail = check_arithmetic(tampered, map_recon_row(tampered))
    assert not ok
    assert "vs Razorpay" in detail


# --------------------------------------------------------------------------
# Mapping
# --------------------------------------------------------------------------


def test_a_payment_row_becomes_a_settlement_line():
    line = map_recon_row(DOCUMENTED_PAYMENT_ROW).as_settlement_item("si_1")
    assert line["item_type"] == "PAYMENT"
    assert line["payment_id"] == "pay_DEXrnipqTmWVGE"
    assert (line["refund_id"], line["adjustment_id"]) == (None, None)
    assert line["credit_amount"] - line["debit_fee"] - line["debit_tax"] == line["net_amount"]


def test_a_refund_row_becomes_a_debit():
    """Refund lines are never positive - the sign is applied once, here."""
    mapped = map_recon_row({
        "entity_id": "rfnd_1", "type": "refund", "debit": 40_000, "credit": 0,
        "amount": 40_000, "currency": "INR", "fee": 0, "tax": 0,
        "settlement_id": "setl_1",
    })
    assert mapped.item_type == "REFUND"
    assert mapped.net_amount == -40_000


def test_an_adjustment_carries_its_own_sign():
    debit = map_recon_row({"entity_id": "adj_1", "type": "adjustment", "debit": 5_000,
                           "credit": 0, "amount": 5_000, "currency": "INR",
                           "fee": 0, "tax": 0, "settlement_id": "setl_1"})
    credit = map_recon_row({"entity_id": "adj_2", "type": "adjustment", "debit": 0,
                            "credit": 5_000, "amount": 5_000, "currency": "INR",
                            "fee": 0, "tax": 0, "settlement_id": "setl_1"})
    assert (debit.net_amount, credit.net_amount) == (-5_000, 5_000)


def test_the_settlement_entity_maps_to_our_batch():
    batch = map_settlement(DOCUMENTED_SETTLEMENT)
    assert batch["settlement_id"] == "setl_7IZKKI4Pnt2kEe"
    assert batch["net_amount"] == 97_100
    assert batch["status"] == "processed"
    assert batch["utr"] == "1568176960vxp0rj"


def test_our_settlement_statuses_are_razorpays():
    """Chosen in Phase 2 from how settlement works, before this API was read."""
    from backend.enums import SettlementStatus

    assert {s.value for s in SettlementStatus} == SETTLEMENT_STATUSES


# --------------------------------------------------------------------------
# What is refused rather than guessed at
# --------------------------------------------------------------------------


def test_route_transfers_are_out_of_scope_and_said_so():
    with pytest.raises(MappingError, match="unsupported recon row type"):
        map_recon_row({"entity_id": "trf_1", "type": "transfer", "debit": 0,
                       "credit": 100, "amount": 100, "currency": "INR",
                       "fee": 0, "tax": 0})


def test_foreign_currency_is_refused_not_treated_as_paise():
    with pytest.raises(MappingError, match="INR only"):
        map_recon_row({**DOCUMENTED_PAYMENT_ROW, "currency": "USD"})


def test_a_row_without_an_entity_id_is_refused():
    with pytest.raises(MappingError, match="entity_id"):
        map_recon_row({**DOCUMENTED_PAYMENT_ROW, "entity_id": None})


def test_non_integer_amounts_are_refused():
    with pytest.raises(MappingError, match="currency subunits"):
        map_recon_row({**DOCUMENTED_PAYMENT_ROW, "fee": 29.0})


def test_an_unknown_settlement_status_is_refused():
    with pytest.raises(MappingError, match="unknown settlement status"):
        map_settlement({**DOCUMENTED_SETTLEMENT, "status": "reversed"})


# --------------------------------------------------------------------------
# Credentials and endpoints
# --------------------------------------------------------------------------


def test_a_live_key_is_refused():
    """This project has no business touching real settlement data."""
    with pytest.raises(RazorpayError, match="non-test key"):
        RazorpayClient(RazorpayCredentials("rzp_live_abcdefgh", "secret"))


def test_missing_credentials_are_refused_with_a_useful_message():
    with pytest.raises(RazorpayError, match="RAZORPAY_KEY_ID"):
        RazorpayClient(RazorpayCredentials("", ""))


def test_credentials_never_render_the_secret():
    creds = RazorpayCredentials("rzp_test_abcdefghij", "supersecretvalue")
    assert "supersecretvalue" not in creds.redacted()
    assert creds.redacted().startswith("rzp_test_")


def test_only_the_three_documented_endpoints_are_called():
    """§43: never invent an endpoint. These were read from the live docs.

    Verified 2026-08-29 against razorpay.com/docs/api/settlements/.
    """
    source = pathlib.Path("backend/razorpay/client.py").read_text()
    paths = set(re.findall(r'f?"(/settlements[^"]*)"', source))
    literal = {p.split("?")[0].replace("{settlement_id}", ":id") for p in paths}
    assert literal == {"/settlements", "/settlements/:id", "/settlements/recon/combined"}
    assert BASE_URL == "https://api.razorpay.com/v1"


def test_the_documented_recon_types_are_the_ones_we_handle():
    from backend.razorpay.mapping import TYPE_MAP

    assert set(TYPE_MAP) | {"transfer"} == RECON_TYPES
