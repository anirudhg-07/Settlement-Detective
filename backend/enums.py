"""Controlled vocabularies for every financial and reconciliation state.

Values are stored as VARCHAR + CHECK constraint rather than native Postgres
enums: adding a value later is then a migration of the constraint, not a
`ALTER TYPE` that cannot run inside a transaction block.
"""

from __future__ import annotations

from enum import StrEnum


class PaymentMethod(StrEnum):
    CARD = "card"
    NETBANKING = "netbanking"
    UPI = "upi"
    WALLET = "wallet"


class OrderStatus(StrEnum):
    CREATED = "created"
    PAID = "paid"
    PARTIALLY_REFUNDED = "partially_refunded"
    REFUNDED = "refunded"
    CANCELLED = "cancelled"


class PaymentStatus(StrEnum):
    CREATED = "created"
    AUTHORIZED = "authorized"
    CAPTURED = "captured"
    FAILED = "failed"
    REFUNDED = "refunded"
    PARTIALLY_REFUNDED = "partially_refunded"


#: Statuses where the merchant is actually owed the money.
#: `created`/`authorized` were never captured; `failed` never happened.
SETTLEABLE_PAYMENT_STATUSES: frozenset[PaymentStatus] = frozenset(
    {
        PaymentStatus.CAPTURED,
        PaymentStatus.REFUNDED,
        PaymentStatus.PARTIALLY_REFUNDED,
    }
)


class RefundStatus(StrEnum):
    CREATED = "created"
    PROCESSED = "processed"
    FAILED = "failed"


class SettlementStatus(StrEnum):
    CREATED = "created"
    PROCESSED = "processed"
    FAILED = "failed"


class SettlementItemType(StrEnum):
    PAYMENT = "PAYMENT"
    REFUND = "REFUND"
    ADJUSTMENT = "ADJUSTMENT"


class AdjustmentType(StrEnum):
    CHARGEBACK = "chargeback"
    CHARGEBACK_REVERSAL = "chargeback_reversal"
    DISPUTE_HOLD = "dispute_hold"
    DISPUTE_RELEASE = "dispute_release"
    MANUAL_CREDIT = "manual_credit"
    MANUAL_DEBIT = "manual_debit"
    PLATFORM_FEE = "platform_fee"


class ReconStatus(StrEnum):
    """Outcome of deterministic reconciliation for a single payment."""

    MATCHED = "MATCHED"
    PENDING_SETTLEMENT = "PENDING_SETTLEMENT"
    EXCEPTION = "EXCEPTION"


class ExceptionType(StrEnum):
    """The taxonomy from the Phase 1 spec, Part F."""

    MISSING_SETTLEMENT = "MISSING_SETTLEMENT"
    DUPLICATE_PAYMENT = "DUPLICATE_PAYMENT"
    MISSING_REFUND = "MISSING_REFUND"
    INCORRECT_REFUND_AMOUNT = "INCORRECT_REFUND_AMOUNT"
    FEE_MISMATCH = "FEE_MISMATCH"
    TAX_MISMATCH = "TAX_MISMATCH"
    PARTIAL_SETTLEMENT = "PARTIAL_SETTLEMENT"
    SETTLEMENT_TIMING = "SETTLEMENT_TIMING"
    UNEXPECTED_ADJUSTMENT = "UNEXPECTED_ADJUSTMENT"
    UNKNOWN_DISCREPANCY = "UNKNOWN_DISCREPANCY"


class ExceptionStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    REVIEW = "REVIEW"
    ESCALATED = "ESCALATED"
    UNRESOLVED = "UNRESOLVED"


class DetectedBy(StrEnum):
    RULE = "RULE"
    RESIDUAL = "RESIDUAL"


class InvestigationMode(StrEnum):
    BASELINE = "BASELINE"
    AI = "AI"


class EvidenceRole(StrEnum):
    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    NEUTRAL = "NEUTRAL"


class DataCondition(StrEnum):
    """Flags raised by the calculator when the *records* are deficient.

    These are deliberately distinct from exceptions: a missing fee row is a
    data-quality problem, and silently folding it into the discrepancy would
    let a data bug masquerade as a financial one.
    """

    MISSING_FEE_RECORD = "MISSING_FEE_RECORD"
    NON_SETTLEABLE_STATUS = "NON_SETTLEABLE_STATUS"
    REFUND_EXCEEDS_PAYMENT = "REFUND_EXCEEDS_PAYMENT"
    FEE_NOT_PER_SCHEDULE = "FEE_NOT_PER_SCHEDULE"
    TAX_NOT_PER_SCHEDULE = "TAX_NOT_PER_SCHEDULE"
