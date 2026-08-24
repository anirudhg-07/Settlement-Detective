"""``ops`` - the source financial records.

This is the merchant's and gateway's view of what happened. The reconciliation
engine only ever reads from here; it never writes back. The AI agent's role can
read these tables and nothing else outside ``recon``.

Sign convention: amounts in ``payments``, ``refunds`` and ``fees`` are stored
**unsigned**. Direction lives in exactly two places - ``adjustments.amount``
(which is genuinely bidirectional) and ``settlement_items.net_amount`` (the
merchant's-bank-account view). Nothing else in the codebase flips a sign.
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.enums import (
    AdjustmentType,
    OrderStatus,
    PaymentMethod,
    PaymentStatus,
    RefundStatus,
    SettlementItemType,
    SettlementStatus,
)
from backend.models.base import (
    SCHEMA_OPS,
    Base,
    enum_col,
    id_col,
    money_col,
    ts_col,
)


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = {"schema": SCHEMA_OPS}

    customer_id: Mapped[str] = id_col(primary_key=True)
    customer_type: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    created_at: Mapped[datetime] = ts_col(nullable=False)

    orders: Mapped[list["Order"]] = relationship(back_populates="customer")


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        sa.CheckConstraint("order_amount >= 0", name="order_amount_non_negative"),
        sa.CheckConstraint("currency = 'INR'", name="currency_inr_only"),
        sa.Index("ix_orders_customer_id", "customer_id"),
        {"schema": SCHEMA_OPS},
    )

    order_id: Mapped[str] = id_col(primary_key=True)
    customer_id: Mapped[str] = id_col(
        sa.ForeignKey(f"{SCHEMA_OPS}.customers.customer_id"), nullable=False
    )
    order_amount: Mapped[int] = money_col(nullable=False)
    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False, default="INR")
    status: Mapped[str] = mapped_column(
        enum_col(OrderStatus, "order_status"), nullable=False
    )
    created_at: Mapped[datetime] = ts_col(nullable=False)

    customer: Mapped["Customer"] = relationship(back_populates="orders")
    payments: Mapped[list["Payment"]] = relationship(back_populates="order")


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        sa.CheckConstraint("amount >= 0", name="amount_non_negative"),
        sa.CheckConstraint("currency = 'INR'", name="currency_inr_only"),
        sa.Index("ix_payments_order_id", "order_id"),
        sa.Index("ix_payments_customer_id", "customer_id"),
        sa.Index("ix_payments_captured_at", "captured_at"),
        {"schema": SCHEMA_OPS},
    )

    payment_id: Mapped[str] = id_col(primary_key=True)
    order_id: Mapped[str] = id_col(
        sa.ForeignKey(f"{SCHEMA_OPS}.orders.order_id"), nullable=False
    )
    customer_id: Mapped[str] = id_col(
        sa.ForeignKey(f"{SCHEMA_OPS}.customers.customer_id"), nullable=False
    )
    amount: Mapped[int] = money_col(nullable=False)
    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False, default="INR")
    payment_method: Mapped[str] = mapped_column(
        enum_col(PaymentMethod, "payment_method"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        enum_col(PaymentStatus, "payment_status"), nullable=False
    )
    created_at: Mapped[datetime] = ts_col(nullable=False)
    captured_at: Mapped[datetime | None] = ts_col(nullable=True)

    order: Mapped["Order"] = relationship(back_populates="payments")
    refunds: Mapped[list["Refund"]] = relationship(back_populates="payment")
    fee: Mapped["Fee | None"] = relationship(back_populates="payment", uselist=False)


class Refund(Base):
    __tablename__ = "refunds"
    __table_args__ = (
        sa.CheckConstraint("amount >= 0", name="amount_non_negative"),
        sa.Index("ix_refunds_payment_id", "payment_id"),
        sa.Index("ix_refunds_order_id", "order_id"),
        {"schema": SCHEMA_OPS},
    )

    refund_id: Mapped[str] = id_col(primary_key=True)
    payment_id: Mapped[str] = id_col(
        sa.ForeignKey(f"{SCHEMA_OPS}.payments.payment_id"), nullable=False
    )
    order_id: Mapped[str] = id_col(
        sa.ForeignKey(f"{SCHEMA_OPS}.orders.order_id"), nullable=False
    )
    amount: Mapped[int] = money_col(nullable=False)
    status: Mapped[str] = mapped_column(
        enum_col(RefundStatus, "refund_status"), nullable=False
    )
    created_at: Mapped[datetime] = ts_col(nullable=False)
    processed_at: Mapped[datetime | None] = ts_col(nullable=True)

    payment: Mapped["Payment"] = relationship(back_populates="refunds")


class Fee(Base):
    __tablename__ = "fees"
    __table_args__ = (
        sa.CheckConstraint("fee_amount >= 0", name="fee_non_negative"),
        sa.CheckConstraint("tax_amount >= 0", name="tax_non_negative"),
        # One fee record per payment. A second one would double-deduct.
        sa.UniqueConstraint("payment_id", name="uq_fees_payment_id"),
        {"schema": SCHEMA_OPS},
    )

    fee_id: Mapped[str] = id_col(primary_key=True)
    payment_id: Mapped[str] = id_col(
        sa.ForeignKey(f"{SCHEMA_OPS}.payments.payment_id"), nullable=False
    )
    fee_amount: Mapped[int] = money_col(nullable=False)
    tax_amount: Mapped[int] = money_col(nullable=False)
    #: The rate actually applied, retained so a mismatch can be explained
    #: rather than merely detected.
    fee_rate_bps: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    created_at: Mapped[datetime] = ts_col(nullable=False)

    payment: Mapped["Payment"] = relationship(back_populates="fee")


class Settlement(Base):
    """A settlement batch - one bank payout, one UTR, many line items."""

    __tablename__ = "settlements"
    __table_args__ = (
        sa.Index("ix_settlements_settlement_date", "settlement_date"),
        {"schema": SCHEMA_OPS},
    )

    settlement_id: Mapped[str] = id_col(primary_key=True)
    #: Signed net payout. Negative is legitimate: a day of heavy refunds can
    #: net a debit from the merchant.
    net_amount: Mapped[int] = money_col(nullable=False)
    utr: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    status: Mapped[str] = mapped_column(
        enum_col(SettlementStatus, "settlement_status"), nullable=False
    )
    settlement_date: Mapped[datetime] = ts_col(nullable=False)
    created_at: Mapped[datetime] = ts_col(nullable=False)

    items: Mapped[list["SettlementItem"]] = relationship(back_populates="settlement")


class Adjustment(Base):
    __tablename__ = "adjustments"
    __table_args__ = (
        sa.Index("ix_adjustments_settlement_id", "settlement_id"),
        sa.Index("ix_adjustments_payment_id", "payment_id"),
        {"schema": SCHEMA_OPS},
    )

    adjustment_id: Mapped[str] = id_col(primary_key=True)
    settlement_id: Mapped[str | None] = id_col(
        sa.ForeignKey(f"{SCHEMA_OPS}.settlements.settlement_id"), nullable=True
    )
    #: Nullable by design - an adjustment with no payment linkage is exactly
    #: the UNEXPECTED_ADJUSTMENT exception.
    payment_id: Mapped[str | None] = id_col(
        sa.ForeignKey(f"{SCHEMA_OPS}.payments.payment_id"), nullable=True
    )
    #: Signed: credit positive, debit negative.
    amount: Mapped[int] = money_col(nullable=False)
    type: Mapped[str] = mapped_column(
        enum_col(AdjustmentType, "adjustment_type"), nullable=False
    )
    reason: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = ts_col(nullable=False)


class SettlementItem(Base):
    """One line of a settlement batch: a payment, a refund, or an adjustment.

    ``net_amount`` is always signed from the merchant's bank account's point of
    view, which makes the batch invariant a plain sum with no per-type logic:

        settlement.net_amount == sum(item.net_amount for item in items)
    """

    __tablename__ = "settlement_items"
    __table_args__ = (
        sa.CheckConstraint(
            "(CASE WHEN payment_id IS NOT NULL THEN 1 ELSE 0 END"
            " + CASE WHEN refund_id IS NOT NULL THEN 1 ELSE 0 END"
            " + CASE WHEN adjustment_id IS NOT NULL THEN 1 ELSE 0 END) = 1",
            name="exactly_one_subject",
        ),
        sa.CheckConstraint("credit_amount >= 0", name="credit_non_negative"),
        sa.CheckConstraint("debit_fee >= 0", name="debit_fee_non_negative"),
        sa.CheckConstraint("debit_tax >= 0", name="debit_tax_non_negative"),
        sa.CheckConstraint(
            "item_type <> 'REFUND' OR net_amount <= 0", name="refund_line_is_a_debit"
        ),
        # A payment may be settled once. A second PAYMENT line for the same
        # payment is a double-count, and is rejected rather than reconciled.
        sa.Index(
            "uq_settlement_items_payment_once",
            "payment_id",
            unique=True,
            postgresql_where=sa.text("item_type = 'PAYMENT'"),
        ),
        sa.Index("ix_settlement_items_settlement_id", "settlement_id"),
        sa.Index("ix_settlement_items_refund_id", "refund_id"),
        {"schema": SCHEMA_OPS},
    )

    item_id: Mapped[str] = id_col(primary_key=True)
    settlement_id: Mapped[str] = id_col(
        sa.ForeignKey(f"{SCHEMA_OPS}.settlements.settlement_id"), nullable=False
    )
    item_type: Mapped[str] = mapped_column(
        enum_col(SettlementItemType, "settlement_item_type"), nullable=False
    )
    payment_id: Mapped[str | None] = id_col(
        sa.ForeignKey(f"{SCHEMA_OPS}.payments.payment_id"), nullable=True
    )
    refund_id: Mapped[str | None] = id_col(
        sa.ForeignKey(f"{SCHEMA_OPS}.refunds.refund_id"), nullable=True
    )
    adjustment_id: Mapped[str | None] = id_col(
        sa.ForeignKey(f"{SCHEMA_OPS}.adjustments.adjustment_id"), nullable=True
    )
    credit_amount: Mapped[int] = money_col(nullable=False, default=0)
    debit_fee: Mapped[int] = money_col(nullable=False, default=0)
    debit_tax: Mapped[int] = money_col(nullable=False, default=0)
    net_amount: Mapped[int] = money_col(nullable=False)
    created_at: Mapped[datetime] = ts_col(nullable=False)

    settlement: Mapped["Settlement"] = relationship(back_populates="items")
