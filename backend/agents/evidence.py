"""Evidence verification and packaging.

Phase 7 checked that a cited record exists. That is not enough: a model can
cite a perfectly real fee row of Rs20 and claim it accounts for Rs500. The
residual closes, the case resolves, and the number came from nowhere.

So for every citation, code derives what that record could *legitimately*
account for - straight from the record itself - and rejects any claim that does
not match one of those amounts. The model chooses which records explain a
discrepancy and how they combine; it never gets to decide what a record says.

That division is the whole safety argument of this product:

    the model reasons about WHICH records matter
    the code decides WHAT those records contain
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import Connection, text

from backend.enums import ExceptionType
from backend.money import format_paise

#: Which ops table holds each citable record type, and its id column.
RECORD_TABLES: dict[str, tuple[str, str]] = {
    "payment": ("ops.payments", "payment_id"),
    "order": ("ops.orders", "order_id"),
    "fee": ("ops.fees", "fee_id"),
    "refund": ("ops.refunds", "refund_id"),
    "adjustment": ("ops.adjustments", "adjustment_id"),
    "settlement": ("ops.settlements", "settlement_id"),
    "settlement_item": ("ops.settlement_items", "item_id"),
}


@dataclass
class Citation:
    """One record the model says accounts for part of the discrepancy."""

    record_type: str
    record_id: str
    claimed: int
    note: str = ""
    verified: bool = False
    reason: str | None = None
    #: What the record itself could account for. Shown to a reviewer so a
    #: rejection is explainable rather than mysterious.
    supported: tuple[int, ...] = ()
    snapshot: dict = field(default_factory=dict)

    @property
    def counts(self) -> int:
        return self.claimed if self.verified else 0


@dataclass
class EvidencePackage:
    """Everything a finance analyst needs to check the conclusion themselves."""

    payment_id: str
    delta: int
    citations: list[Citation]
    unexplained: int
    cause_type: str

    @property
    def explained(self) -> int:
        return sum(c.counts for c in self.citations)

    @property
    def verified(self) -> list[Citation]:
        return [c for c in self.citations if c.verified]

    @property
    def rejected(self) -> list[Citation]:
        return [c for c in self.citations if not c.verified]

    def calculation_lines(self) -> list[str]:
        """The arithmetic, written out. This is what Screen 4 renders."""
        lines = [f"discrepancy to explain{format_paise(self.delta):>20}"]
        for c in self.verified:
            label = f"  {c.record_type} {c.record_id}"
            lines.append(f"{label:<42}{format_paise(-c.claimed):>12}")
        lines.append(f"{'unexplained remainder':<42}{format_paise(self.unexplained):>12}")
        return lines


# --------------------------------------------------------------------------
# What a record can actually support
# --------------------------------------------------------------------------


def _settled_totals(conn: Connection, payment_id: str) -> tuple[int, int]:
    """Fee and tax actually deducted across the payment's settled lines.

    Postgres returns NUMERIC for SUM over BIGINT, which arrives as ``Decimal``.
    Money is integer paise everywhere in this codebase, so it is cast back at
    the boundary rather than allowed to leak inward - the money layer rejects
    Decimals outright, which is how this was caught.
    """
    row = conn.execute(
        text(
            "SELECT COALESCE(SUM(i.debit_fee), 0) AS fee,"
            "       COALESCE(SUM(i.debit_tax), 0) AS tax"
            "  FROM ops.settlement_items i"
            "  JOIN ops.settlements s USING (settlement_id)"
            " WHERE i.payment_id = :p AND i.item_type = 'PAYMENT'"
            "   AND s.status = 'processed'"
        ),
        {"p": payment_id},
    ).mappings().one()
    return int(row["fee"]), int(row["tax"])


def _has_no_settled_credit(conn: Connection, payment_id: str) -> bool:
    """True when no processed batch ever credited this payment."""
    return not conn.execute(
        text(
            "SELECT 1 FROM ops.settlement_items i"
            "  JOIN ops.settlements s USING (settlement_id)"
            " WHERE i.payment_id = :p AND i.item_type = 'PAYMENT'"
            "   AND s.status = 'processed' LIMIT 1"
        ),
        {"p": payment_id},
    ).scalar()


def supported_contributions(
    conn: Connection, record_type: str, record_id: str, payment_id: str
) -> tuple[tuple[int, ...], dict]:
    """Every amount this record could honestly account for, and its values.

    Derived from the record and its settlement lines - never from the model.
    Both signs are offered where the direction depends on which way the fault
    ran (a refund not debited raises the settlement; one debited twice lowers
    it), because the direction is a fact about the case, not about the record.
    """
    kind = record_type.lower()

    if kind == "fee":
        row = conn.execute(
            text(
                "SELECT payment_id, fee_amount, tax_amount FROM ops.fees"
                " WHERE fee_id = :i"
            ),
            {"i": record_id},
        ).mappings().one_or_none()
        if row is None:
            return (), {}
        deducted_fee, deducted_tax = _settled_totals(conn, row["payment_id"])
        fee_gap = row["fee_amount"] - deducted_fee
        tax_gap = row["tax_amount"] - deducted_tax
        amounts = {
            fee_gap, tax_gap, fee_gap + tax_gap,
            -row["fee_amount"], -row["tax_amount"],
            -(row["fee_amount"] + row["tax_amount"]),
        }
        return tuple(sorted(a for a in amounts if a != 0)), {
            "recorded_fee": row["fee_amount"],
            "recorded_tax": row["tax_amount"],
            "deducted_fee": deducted_fee,
            "deducted_tax": deducted_tax,
        }

    if kind == "refund":
        row = conn.execute(
            text(
                "SELECT amount, status, processed_at FROM ops.refunds"
                " WHERE refund_id = :i"
            ),
            {"i": record_id},
        ).mappings().one_or_none()
        if row is None:
            return (), {}
        debited = -int(
            conn.execute(
                text(
                    "SELECT COALESCE(SUM(i.net_amount), 0) FROM ops.settlement_items i"
                    "  JOIN ops.settlements s USING (settlement_id)"
                    " WHERE i.refund_id = :i AND s.status = 'processed'"
                ),
                {"i": record_id},
            ).scalar()
        )
        amounts = {row["amount"], -row["amount"], row["amount"] - debited,
                   debited - row["amount"]}
        return tuple(sorted(a for a in amounts if a != 0)), {
            "recorded_amount": row["amount"],
            "status": row["status"],
            "actually_debited": debited,
        }

    if kind == "adjustment":
        row = conn.execute(
            text(
                "SELECT amount, type, reason, settlement_id FROM ops.adjustments"
                " WHERE adjustment_id = :i"
            ),
            {"i": record_id},
        ).mappings().one_or_none()
        if row is None:
            return (), {}
        return tuple(sorted({row["amount"], -row["amount"]})), {
            "amount": row["amount"],
            "type": row["type"],
            "reason": row["reason"],
            "settlement_id": row["settlement_id"],
        }

    if kind == "settlement_item":
        row = conn.execute(
            text(
                "SELECT i.item_type, i.credit_amount, i.debit_fee, i.debit_tax,"
                "       i.net_amount, i.payment_id, s.status"
                "  FROM ops.settlement_items i JOIN ops.settlements s USING (settlement_id)"
                " WHERE i.item_id = :i"
            ),
            {"i": record_id},
        ).mappings().one_or_none()
        if row is None:
            return (), {}
        amounts = {row["net_amount"], -row["net_amount"], row["credit_amount"],
                   -row["credit_amount"]}
        if row["payment_id"]:
            fee = conn.execute(
                text("SELECT fee_amount, tax_amount FROM ops.fees WHERE payment_id = :p"),
                {"p": row["payment_id"]},
            ).mappings().one_or_none()
            if fee:
                amounts |= {
                    fee["fee_amount"] - row["debit_fee"],
                    fee["tax_amount"] - row["debit_tax"],
                    (fee["fee_amount"] - row["debit_fee"])
                    + (fee["tax_amount"] - row["debit_tax"]),
                }
        return tuple(sorted(a for a in amounts if a != 0)), {
            "type": row["item_type"],
            "net": row["net_amount"],
            "fee_deducted": row["debit_fee"],
            "tax_deducted": row["debit_tax"],
            "batch_status": row["status"],
        }

    if kind == "settlement":
        row = conn.execute(
            text(
                "SELECT status, net_amount FROM ops.settlements WHERE settlement_id = :i"
            ),
            {"i": record_id},
        ).mappings().one_or_none()
        if row is None:
            return (), {}
        held = int(
            conn.execute(
                text(
                    "SELECT COALESCE(SUM(net_amount), 0) FROM ops.settlement_items"
                    " WHERE settlement_id = :i AND payment_id = :p"
                ),
                {"i": record_id, "p": payment_id},
            ).scalar()
        )
        amounts = {held, -held, row["net_amount"], -row["net_amount"]}
        return tuple(sorted(a for a in amounts if a != 0)), {
            "status": row["status"],
            "batch_net": row["net_amount"],
            "this_payments_share": held,
        }

    return (), {}


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------


def verify_citations(
    conn: Connection,
    raw: list[dict],
    *,
    payment_id: str,
    delta: int,
    cause_type: str,
    tolerance: int,
) -> list[Citation]:
    """Check each citation against what its record actually contains."""
    out: list[Citation] = []
    for item in raw:
        record_type = str(item.get("record_type", "")).lower()
        record_id = item.get("record_id")
        claimed = item.get("amount_paise")
        citation = Citation(
            record_type=record_type,
            record_id=record_id if isinstance(record_id, str) else str(record_id),
            claimed=claimed if isinstance(claimed, int) else 0,
            note=str(item.get("note") or "")[:2000],
        )

        if record_type not in RECORD_TABLES:
            citation.reason = f"unknown record type {record_type!r}"
            out.append(citation); continue
        if not isinstance(record_id, str):
            citation.reason = "record_id must be a string"
            out.append(citation); continue
        if not isinstance(claimed, int):
            citation.reason = "amount_paise must be an integer"
            out.append(citation); continue

        table, column = RECORD_TABLES[record_type]
        if not conn.execute(
            text(f"SELECT 1 FROM {table} WHERE {column} = :v"), {"v": record_id}
        ).scalar():
            citation.reason = f"no such record in {table}"
            out.append(citation); continue

        # Some exceptions reconcile perfectly: a duplicate charge, a settlement
        # that arrived late, a deduction nobody authorised. The money adds up -
        # it simply should not have moved, or should have moved sooner. There
        # is no amount to apportion, so evidence here CORROBORATES rather than
        # accounts, and a citation of zero against a real record stands.
        #
        # This opens no hole: with no discrepancy, corroborating evidence
        # cannot falsely close one. The residual arithmetic is untouched.
        if delta == 0 and claimed == 0:
            citation.verified = True
            citation.supported = (0,)
            citation.snapshot = {"corroborating": True}
            out.append(citation); continue

        # A record cannot explain itself. The one exception is a payment that
        # never settled, where the explanation IS the absence - and the
        # database confirms the absence, not the model.
        if record_id == payment_id:
            if delta == 0 and claimed == 0:
                # Naming the payment under investigation is identification,
                # not explanation - harmless when there is no money at stake.
                citation.verified = True
                citation.supported = (0,)
                citation.snapshot = {"corroborating": True}
                out.append(citation); continue
            if cause_type == ExceptionType.MISSING_SETTLEMENT.value and (
                _has_no_settled_credit(conn, payment_id)
            ):
                citation.verified = abs(claimed - delta) <= tolerance
                citation.supported = (delta,)
                citation.snapshot = {"settled_credit_lines": 0}
                if not citation.verified:
                    citation.reason = (
                        "an unsettled payment accounts for the whole discrepancy "
                        f"({format_paise(delta)}), not {format_paise(claimed)}"
                    )
            else:
                citation.reason = (
                    "a payment cannot be the evidence for its own discrepancy; "
                    "cite the record that accounts for the money"
                )
            out.append(citation); continue

        supported, snapshot = supported_contributions(
            conn, record_type, record_id, payment_id
        )
        citation.supported = supported
        citation.snapshot = snapshot
        if not supported:
            citation.reason = (
                f"a {record_type} record carries no amount that could account "
                "for a discrepancy"
            )
        elif any(abs(claimed - s) <= tolerance for s in supported):
            citation.verified = True
        else:
            citation.reason = (
                f"claimed {format_paise(claimed)}, but this record can only "
                f"account for {', '.join(format_paise(s) for s in supported)}"
            )
        out.append(citation)
    return out


def build_package(
    citations: list[Citation], *, payment_id: str, delta: int, cause_type: str
) -> EvidencePackage:
    explained = sum(c.counts for c in citations)
    return EvidencePackage(
        payment_id=payment_id,
        delta=delta,
        citations=citations,
        unexplained=delta - explained,
        cause_type=cause_type,
    )
