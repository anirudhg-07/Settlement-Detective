"""The agent's tool surface.

Everything the investigator can see or do passes through here. Four rules
shape it:

1. **Least privilege.** Every query runs on the ``sd_agent`` connection, which
   has read-only access to `ops`, no write access to any financial record, and
   *no grant at all* on schema `gt`. A tool that tried to read ground truth
   would fail with `permission denied`, not quietly succeed.

2. **No arithmetic in the model.** `calculate_expected_settlement` runs the
   same audited functions the reconciliation engine uses. The agent is told
   what the numbers are; it never works them out.

3. **Bundled by default.** `get_case_bundle` returns the payment together with
   its order, fee, refunds, adjustments and settlement lines in a single call.
   Six round-trips per investigation would exhaust a 500-request daily quota
   after 83 cases; four gets us to 125.

4. **Arguments are validated before execution.** Identifiers are pattern-
   checked and looked up; a malformed or unknown id comes back as a structured
   error the model can react to, never as an exception that kills the run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

from sqlalchemy import Connection, text

from backend.config import FinancialConfig
from backend.enums import ExceptionType, PaymentMethod, PaymentStatus, RefundStatus
from backend.money import format_paise
from backend.reconciliation.settlement_math import (
    AdjustmentFact,
    PaymentFacts,
    RefundFact,
    expected_net_settlement,
)
from backend.reconciliation.timing import settlement_deadline, settlement_eligible_on

ID_PATTERN = re.compile(r"^[a-z]+_[A-Za-z0-9]{4,32}$")


class ToolError(Exception):
    """A tool could not run. Returned to the model as data, never raised at it."""


def _validate_id(value: Any, prefix: str, field: str) -> str:
    if not isinstance(value, str) or not ID_PATTERN.match(value):
        raise ToolError(f"{field} must look like '{prefix}_XXXXXXXX', got {value!r}")
    if not value.startswith(f"{prefix}_"):
        raise ToolError(f"{field} must start with '{prefix}_', got {value!r}")
    return value


def _money(paise: int | None) -> dict | None:
    if paise is None:
        return None
    return {"paise": paise, "rupees": format_paise(paise)}


@dataclass
class ToolContext:
    """Everything a tool needs. One per investigation."""

    conn: Connection            # the sd_agent connection
    cfg: FinancialConfig
    as_of: date


# --------------------------------------------------------------------------
# Queries
# --------------------------------------------------------------------------


def _payment_row(ctx: ToolContext, payment_id: str) -> dict:
    row = ctx.conn.execute(
        text(
            "SELECT payment_id, order_id, customer_id, amount, payment_method,"
            "       status, created_at, captured_at FROM ops.payments"
            " WHERE payment_id = :p"
        ),
        {"p": payment_id},
    ).mappings().one_or_none()
    if row is None:
        raise ToolError(f"no payment with id {payment_id}")
    return dict(row)


def _facts(ctx: ToolContext, payment_id: str) -> tuple[PaymentFacts, dict]:
    """Assemble the payment's records into the shape the calculator expects."""
    payment = _payment_row(ctx, payment_id)
    fee = ctx.conn.execute(
        text(
            "SELECT fee_id, fee_amount, tax_amount, fee_rate_bps FROM ops.fees"
            " WHERE payment_id = :p"
        ),
        {"p": payment_id},
    ).mappings().one_or_none()
    refunds = [
        dict(r)
        for r in ctx.conn.execute(
            text(
                "SELECT refund_id, amount, status, created_at, processed_at"
                "  FROM ops.refunds WHERE payment_id = :p ORDER BY created_at"
            ),
            {"p": payment_id},
        ).mappings()
    ]
    adjustments = [
        dict(r)
        for r in ctx.conn.execute(
            text(
                "SELECT adjustment_id, settlement_id, amount, type, reason, created_at"
                "  FROM ops.adjustments WHERE payment_id = :p"
            ),
            {"p": payment_id},
        ).mappings()
    ]
    facts = PaymentFacts(
        payment_id=payment_id,
        amount=payment["amount"],
        method=PaymentMethod(payment["payment_method"]),
        status=PaymentStatus(payment["status"]),
        captured_at=payment["captured_at"],
        fee=fee["fee_amount"] if fee else None,
        tax=fee["tax_amount"] if fee else None,
        refunds=tuple(
            RefundFact(r["refund_id"], r["amount"], RefundStatus(r["status"]),
                       processed_at=r["processed_at"])
            for r in refunds
        ),
        adjustments=tuple(
            AdjustmentFact(a["adjustment_id"], a["amount"], a["type"],
                           created_at=a["created_at"])
            for a in adjustments
        ),
    )
    return facts, {
        "payment": payment,
        "fee": dict(fee) if fee else None,
        "refunds": refunds,
        "adjustments": adjustments,
    }


def _settlement_lines(ctx: ToolContext, payment_id: str, refund_ids: list[str],
                      adjustment_ids: list[str]) -> list[dict]:
    return [
        dict(r)
        for r in ctx.conn.execute(
            text(
                "SELECT i.item_id, i.item_type, i.payment_id, i.refund_id,"
                "       i.adjustment_id, i.credit_amount, i.debit_fee, i.debit_tax,"
                "       i.net_amount, s.settlement_id, s.status AS batch_status,"
                "       s.settlement_date AS batch_date, s.utr"
                "  FROM ops.settlement_items i"
                "  JOIN ops.settlements s USING (settlement_id)"
                " WHERE i.payment_id = :p"
                "    OR i.refund_id = ANY(:rids)"
                "    OR i.adjustment_id = ANY(:aids)"
                " ORDER BY s.settlement_date"
            ),
            {"p": payment_id, "rids": refund_ids or [""], "aids": adjustment_ids or [""]},
        ).mappings()
    ]


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------


def get_case_bundle(ctx: ToolContext, payment_id: str) -> dict:
    """Everything about one payment, in a single call."""
    payment_id = _validate_id(payment_id, "pay", "payment_id")
    facts, records = _facts(ctx, payment_id)
    lines = _settlement_lines(
        ctx,
        payment_id,
        [r["refund_id"] for r in records["refunds"]],
        [a["adjustment_id"] for a in records["adjustments"]],
    )
    expected = expected_net_settlement(facts, ctx.cfg, as_of=ctx.as_of)
    settled = [l for l in lines if l["batch_status"] == "processed"]
    actual = sum(l["net_amount"] for l in settled)

    payment = records["payment"]
    return {
        "payment": {
            "payment_id": payment_id,
            "order_id": payment["order_id"],
            "customer_id": payment["customer_id"],
            "amount": _money(payment["amount"]),
            "method": payment["payment_method"],
            "status": payment["status"],
            "captured_at": str(payment["captured_at"]),
            "settlement_due_by": str(settlement_deadline(payment["captured_at"], ctx.cfg))
            if payment["captured_at"] else None,
        },
        "fee": (
            {
                "fee_id": records["fee"]["fee_id"],
                "fee": _money(records["fee"]["fee_amount"]),
                "tax": _money(records["fee"]["tax_amount"]),
                "rate_bps": records["fee"]["fee_rate_bps"],
            }
            if records["fee"] else None
        ),
        "refunds": [
            {
                "refund_id": r["refund_id"],
                "amount": _money(r["amount"]),
                "status": r["status"],
                "processed_at": str(r["processed_at"]) if r["processed_at"] else None,
                "debit_due_by": str(settlement_eligible_on(r["processed_at"], ctx.cfg))
                if r["processed_at"] else None,
            }
            for r in records["refunds"]
        ],
        "adjustments": [
            {
                "adjustment_id": a["adjustment_id"],
                "amount": _money(a["amount"]),
                "type": a["type"],
                "reason": a["reason"],
                "settlement_id": a["settlement_id"],
            }
            for a in records["adjustments"]
        ],
        "settlement_lines": [
            {
                "item_id": l["item_id"],
                "type": l["item_type"],
                "settlement_id": l["settlement_id"],
                "batch_status": l["batch_status"],
                "batch_date": str(l["batch_date"].date()),
                "credit": _money(l["credit_amount"]),
                "fee_deducted": _money(l["debit_fee"]),
                "tax_deducted": _money(l["debit_tax"]),
                "net": _money(l["net_amount"]),
                "counts_toward_actual": l["batch_status"] == "processed",
            }
            for l in lines
        ],
        "reconciliation": {
            "expected_net": _money(expected.expected_net),
            "actual_net": _money(actual),
            "delta": _money(actual - expected.expected_net),
            "note": "delta = actual - expected. Negative means money is missing.",
            "expected_breakdown": {k: _money(v) for k, v in expected.components().items()},
            "refunds_not_yet_due": _money(expected.refunds_not_yet_due),
            "data_flags": [f.value for f in expected.flags],
        },
    }


def calculate_expected_settlement(ctx: ToolContext, payment_id: str) -> dict:
    """Recompute the expectation with the audited financial functions."""
    payment_id = _validate_id(payment_id, "pay", "payment_id")
    facts, _ = _facts(ctx, payment_id)
    expected = expected_net_settlement(facts, ctx.cfg, as_of=ctx.as_of)
    return {
        "payment_id": payment_id,
        "expected_net": _money(expected.expected_net),
        "components": {k: _money(v) for k, v in expected.components().items()},
        "computed_by": "deterministic engine, not the model",
    }


def find_related_transactions(ctx: ToolContext, payment_id: str) -> dict:
    """Other payments on the same order, and their refunds.

    The hop that finds a duplicate charge, or a refund recorded against a
    sibling payment rather than this one.
    """
    payment_id = _validate_id(payment_id, "pay", "payment_id")
    payment = _payment_row(ctx, payment_id)
    siblings = [
        dict(r)
        for r in ctx.conn.execute(
            text(
                "SELECT payment_id, amount, status, payment_method, captured_at"
                "  FROM ops.payments WHERE order_id = :o AND payment_id <> :p"
                " ORDER BY captured_at"
            ),
            {"o": payment["order_id"], "p": payment_id},
        ).mappings()
    ]
    order_refunds = [
        dict(r)
        for r in ctx.conn.execute(
            text(
                "SELECT refund_id, payment_id, amount, status, processed_at"
                "  FROM ops.refunds WHERE order_id = :o"
            ),
            {"o": payment["order_id"]},
        ).mappings()
    ]
    return {
        "order_id": payment["order_id"],
        "this_payment": {"payment_id": payment_id, "amount": _money(payment["amount"])},
        "other_payments_on_this_order": [
            {
                "payment_id": s["payment_id"],
                "amount": _money(s["amount"]),
                "status": s["status"],
                "method": s["payment_method"],
                "captured_at": str(s["captured_at"]),
                "same_amount_as_this": s["amount"] == payment["amount"],
            }
            for s in siblings
        ],
        "all_refunds_on_this_order": [
            {
                "refund_id": r["refund_id"],
                "against_payment": r["payment_id"],
                "belongs_to_this_payment": r["payment_id"] == payment_id,
                "amount": _money(r["amount"]),
                "status": r["status"],
            }
            for r in order_refunds
        ],
    }


def search_batch_adjustments(ctx: ToolContext, settlement_id: str) -> dict:
    """Every adjustment sitting in a settlement batch, linked or not.

    Deductions are sometimes booked against the batch with no `payment_id` at
    all, naming the payment only in free text. Nothing keyed on payment_id
    will ever find those.
    """
    settlement_id = _validate_id(settlement_id, "setl", "settlement_id")
    rows = [
        dict(r)
        for r in ctx.conn.execute(
            text(
                "SELECT adjustment_id, payment_id, amount, type, reason, created_at"
                "  FROM ops.adjustments WHERE settlement_id = :s"
            ),
            {"s": settlement_id},
        ).mappings()
    ]
    return {
        "settlement_id": settlement_id,
        "adjustments": [
            {
                "adjustment_id": a["adjustment_id"],
                "amount": _money(a["amount"]),
                "type": a["type"],
                "reason": a["reason"],
                "linked_payment_id": a["payment_id"],
                "is_unlinked": a["payment_id"] is None,
            }
            for a in rows
        ],
        "count": len(rows),
    }


def get_settlement(ctx: ToolContext, settlement_id: str) -> dict:
    """A settlement batch and every line in it."""
    settlement_id = _validate_id(settlement_id, "setl", "settlement_id")
    batch = ctx.conn.execute(
        text(
            "SELECT settlement_id, net_amount, utr, status, settlement_date"
            "  FROM ops.settlements WHERE settlement_id = :s"
        ),
        {"s": settlement_id},
    ).mappings().one_or_none()
    if batch is None:
        raise ToolError(f"no settlement with id {settlement_id}")
    lines = [
        dict(r)
        for r in ctx.conn.execute(
            text(
                "SELECT item_id, item_type, payment_id, refund_id, adjustment_id,"
                "       net_amount FROM ops.settlement_items WHERE settlement_id = :s"
            ),
            {"s": settlement_id},
        ).mappings()
    ]
    return {
        "settlement_id": settlement_id,
        "status": batch["status"],
        "paid_out": batch["status"] == "processed",
        "settlement_date": str(batch["settlement_date"].date()),
        "utr": batch["utr"],
        "net_amount": _money(batch["net_amount"]),
        "line_count": len(lines),
        "lines": [
            {
                "item_id": l["item_id"],
                "type": l["item_type"],
                "subject": l["payment_id"] or l["refund_id"] or l["adjustment_id"],
                "net": _money(l["net_amount"]),
            }
            for l in lines
        ],
    }


#: Terminal tool. Calling it ends the investigation.
SUBMIT_FINDING = "submit_finding"

TOOL_FUNCTIONS: dict[str, Callable[..., dict]] = {
    "get_case_bundle": get_case_bundle,
    "calculate_expected_settlement": calculate_expected_settlement,
    "find_related_transactions": find_related_transactions,
    "search_batch_adjustments": search_batch_adjustments,
    "get_settlement": get_settlement,
}

_PAYMENT_ARG = {
    "type": "object",
    "properties": {"payment_id": {"type": "string", "description": "e.g. pay_9fK2xQ7bLm3aWd"}},
    "required": ["payment_id"],
}
_SETTLEMENT_ARG = {
    "type": "object",
    "properties": {"settlement_id": {"type": "string", "description": "e.g. setl_3xQ9mK7bLw2aFd"}},
    "required": ["settlement_id"],
}

TOOL_SCHEMAS: list[dict] = [
    {
        "name": "get_case_bundle",
        "description": (
            "Start here. Returns the payment with its order, fee, refunds, "
            "adjustments and every settlement line, plus the expected/actual/delta "
            "already computed. Usually enough on its own."
        ),
        "parameters": _PAYMENT_ARG,
    },
    {
        "name": "calculate_expected_settlement",
        "description": (
            "Recompute what this payment should have settled for, using the "
            "audited financial engine. Use this instead of doing arithmetic."
        ),
        "parameters": _PAYMENT_ARG,
    },
    {
        "name": "find_related_transactions",
        "description": (
            "Other payments on the same order and all refunds across that order. "
            "Use when the payment's own records do not explain the discrepancy - "
            "for a suspected duplicate charge, or a refund booked against a "
            "sibling payment."
        ),
        "parameters": _PAYMENT_ARG,
    },
    {
        "name": "search_batch_adjustments",
        "description": (
            "Every adjustment in a settlement batch, including ones with no "
            "payment link. Use when money is missing from a payment and its own "
            "records show no cause - the deduction may be booked against the "
            "batch instead."
        ),
        "parameters": _SETTLEMENT_ARG,
    },
    {
        "name": "get_settlement",
        "description": (
            "A settlement batch and its lines, including whether it has actually "
            "been paid out. A line in a batch that is still 'created' is money "
            "scheduled, not money received."
        ),
        "parameters": _SETTLEMENT_ARG,
    },
    {
        "name": SUBMIT_FINDING,
        "description": (
            "Report your conclusion and end the investigation. Cite a record for "
            "every rupee you claim to explain. If the records do not account for "
            "the discrepancy, set unresolved=true and say so - that is a correct "
            "answer, not a failure."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "cause_type": {
                    "type": "string",
                    "enum": [t.value for t in ExceptionType],
                    "description": "UNKNOWN_DISCREPANCY if nothing explains it.",
                },
                "summary": {
                    "type": "string",
                    "description": "Two or three sentences a finance analyst can act on.",
                },
                "evidence": {
                    "type": "array",
                    "description": (
                        "One entry per record that accounts for part of the delta. "
                        "The amounts must sum to the delta for the case to resolve."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "record_type": {
                                "type": "string",
                                "enum": ["payment", "order", "fee", "refund",
                                         "adjustment", "settlement", "settlement_item"],
                            },
                            "record_id": {"type": "string"},
                            "amount_paise": {
                                "type": "integer",
                                "description": "Signed contribution to the delta.",
                            },
                            "note": {"type": "string"},
                        },
                        "required": ["record_type", "record_id", "amount_paise", "note"],
                    },
                },
                "unresolved": {
                    "type": "boolean",
                    "description": "True when the evidence does not account for the delta.",
                },
                "confidence": {
                    "type": "integer",
                    "description": "0-100, your own certainty. Recorded, but the "
                                   "decision is made from the evidence, not from this.",
                },
            },
            "required": ["cause_type", "summary", "evidence", "unresolved", "confidence"],
        },
    },
]


def run_tool(ctx: ToolContext, call_name: str, args: dict) -> dict:
    """Dispatch one validated tool call. Unknown names are refused."""
    fn = TOOL_FUNCTIONS.get(call_name)
    if fn is None:
        raise ToolError(
            f"no tool named {call_name!r}. Available: "
            f"{', '.join(sorted(TOOL_FUNCTIONS) + [SUBMIT_FINDING])}"
        )
    allowed = set(TOOL_SCHEMAS[[s['name'] for s in TOOL_SCHEMAS].index(call_name)]
                  ["parameters"]["properties"])
    unexpected = set(args) - allowed
    if unexpected:
        raise ToolError(f"{call_name} got unexpected arguments: {sorted(unexpected)}")
    return fn(ctx, **args)
