"""Map Razorpay settlement data onto this project's model.

The recon report returns one row per settled payment, refund, transfer or
adjustment, carrying `entity_id`, `type`, `debit`, `credit`, `fee`, `tax` and
`settlement_id`. That is the same shape as our `settlement_items` table.

It is worth being precise about why. The model was designed in Phase 1 from how
settlement actually works, before any of this was read, and three decisions
made then match the API exactly:

* **Amounts are integer currency subunits.** Razorpay reports paise; so do we.
* **Settlement is a batch with line items**, not one row per payment - which is
  what the recon report is.
* **Settlement status is `created` / `processed` / `failed`** - the same three
  values our enum already had.

The mapping is short because the models agree, and that agreement is the point
of this phase: it is evidence the synthetic environment reflects the real one
rather than a convenient invention.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from backend.enums import SettlementItemType
from backend.reconciliation.settlement_math import (
    adjustment_item_net,
    payment_item_net,
    refund_item_net,
)

#: Razorpay recon `type` -> our settlement item type. `transfer` belongs to
#: Route (split payments), which this product does not model; those rows are
#: reported as unsupported rather than force-fitted into a category.
TYPE_MAP: dict[str, SettlementItemType] = {
    "payment": SettlementItemType.PAYMENT,
    "refund": SettlementItemType.REFUND,
    "adjustment": SettlementItemType.ADJUSTMENT,
}


class MappingError(ValueError):
    """A row cannot be represented in this model, and is not guessed at."""


@dataclass
class MappedItem:
    item_type: str
    entity_id: str
    settlement_id: str | None
    credit: int
    debit_fee: int
    debit_tax: int
    net_amount: int
    currency: str
    settled_at: datetime | None
    source_type: str

    def as_settlement_item(self, item_id: str) -> dict:
        """A row shaped for `ops.settlement_items`."""
        return {
            "item_id": item_id,
            "settlement_id": self.settlement_id,
            "item_type": self.item_type,
            "payment_id": self.entity_id if self.item_type == "PAYMENT" else None,
            "refund_id": self.entity_id if self.item_type == "REFUND" else None,
            "adjustment_id": self.entity_id if self.item_type == "ADJUSTMENT" else None,
            "credit_amount": self.credit,
            "debit_fee": self.debit_fee,
            "debit_tax": self.debit_tax,
            "net_amount": self.net_amount,
            "created_at": self.settled_at,
        }


def _int(row: dict, key: str) -> int:
    value = row.get(key) or 0
    if isinstance(value, bool) or not isinstance(value, int):
        raise MappingError(f"{key} must be an integer of currency subunits, got {value!r}")
    return value


def _timestamp(value: int | None) -> datetime | None:
    return datetime.fromtimestamp(value, tz=timezone.utc) if value else None


def map_recon_row(row: dict) -> MappedItem:
    """Convert one recon report row into our settlement line."""
    source_type = row.get("type")
    if source_type not in TYPE_MAP:
        raise MappingError(
            f"unsupported recon row type {source_type!r}; this model covers "
            f"{sorted(TYPE_MAP)} (Route transfers are out of scope)"
        )
    currency = (row.get("currency") or "INR").upper()
    if currency != "INR":
        raise MappingError(
            f"row is in {currency}; v1 reconciles INR only and will not treat "
            "another currency's minor units as paise"
        )

    entity_id = row.get("entity_id")
    if not isinstance(entity_id, str) or not entity_id:
        raise MappingError("entity_id is missing")

    credit, debit = _int(row, "credit"), _int(row, "debit")
    fee, tax = _int(row, "fee"), _int(row, "tax")
    amount = _int(row, "amount")
    item_type = TYPE_MAP[source_type]

    # The sign is applied here, once, exactly as it is for synthetic data.
    if item_type is SettlementItemType.PAYMENT:
        # `credit` is ALREADY net of fee and tax - the documented example is
        # amount 100000, fee 2900, tax 0, credit 97100. Treating it as gross
        # and deducting again would take the fee twice off every payment.
        # `amount` is the gross, and is what our line's credit column holds.
        net = payment_item_net(amount, fee, tax)
        credit = amount
    elif item_type is SettlementItemType.REFUND:
        net = refund_item_net(debit)
        credit, fee, tax = 0, 0, 0
    else:
        net = adjustment_item_net(credit - debit)
        credit, fee, tax = 0, 0, 0

    return MappedItem(
        item_type=item_type.value,
        entity_id=entity_id,
        settlement_id=row.get("settlement_id"),
        credit=credit,
        debit_fee=fee,
        debit_tax=tax,
        net_amount=net,
        currency=currency,
        settled_at=_timestamp(row.get("settled_at")),
        source_type=source_type,
    )


def map_settlement(row: dict) -> dict:
    """Convert a Settlement entity into an `ops.settlements` row."""
    status = row.get("status")
    if status not in {"created", "processed", "failed"}:
        raise MappingError(f"unknown settlement status {status!r}")
    created = _timestamp(row.get("created_at"))
    return {
        "settlement_id": row["id"],
        "net_amount": _int(row, "amount"),
        "utr": row.get("utr"),
        "status": status,
        "settlement_date": created,
        "created_at": created,
    }


def check_arithmetic(row: dict, mapped: MappedItem) -> tuple[bool, str]:
    """Does our settlement arithmetic reproduce Razorpay's own figures?

    Razorpay reports the gross in `amount`, the deductions in `fee` and `tax`,
    and the net it actually credited in `credit`. Running our own
    `payment_item_net` over the gross and the deductions must land on their
    credited figure. If it does not, our financial model is wrong about the
    real world and every number this project reports is built on sand.
    """
    if mapped.item_type != SettlementItemType.PAYMENT.value:
        return True, "not a payment row"
    # Razorpay states the net itself, in `credit`. Our own formula, run over
    # the gross and the fees, must land on the same number.
    theirs = _int(row, "credit")
    if mapped.net_amount == theirs:
        return True, f"agrees: {theirs} paise"
    return False, f"ours {mapped.net_amount} vs Razorpay {theirs}"
