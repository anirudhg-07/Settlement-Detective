"""Write-time guards.

These reject impossible financial states before they reach the database, so
that a data bug can never present itself later as a financial discrepancy.
"""

from __future__ import annotations

from typing import Iterable

from backend.enums import RefundStatus

SUPPORTED_CURRENCIES = frozenset({"INR"})


class GuardViolation(ValueError):
    """An operation would create a financially impossible record."""


def validate_currency(currency: str) -> str:
    """v1 is INR-only. A foreign-currency row must fail loudly rather than be
    reconciled as if its minor units were paise."""
    normalised = (currency or "").upper()
    if normalised not in SUPPORTED_CURRENCIES:
        raise GuardViolation(
            f"unsupported currency {currency!r}; v1 supports {sorted(SUPPORTED_CURRENCIES)}"
        )
    return normalised


def validate_non_negative(name: str, amount: int) -> int:
    if isinstance(amount, bool) or not isinstance(amount, int):
        raise GuardViolation(f"{name} must be an int paise amount, got {amount!r}")
    if amount < 0:
        raise GuardViolation(f"{name} must be non-negative, got {amount}")
    return amount


def validate_refund_total(
    payment_amount: int, refund_amounts: Iterable[tuple[int, RefundStatus]]
) -> int:
    """Reject a refund set that exceeds the payment.

    Counts both processed and pending refunds: over-refunding should be
    impossible to *request*, not merely impossible to complete.
    """
    total = sum(
        amount
        for amount, status in refund_amounts
        if RefundStatus(status) is not RefundStatus.FAILED
    )
    if total > payment_amount:
        raise GuardViolation(
            f"refunds total {total} paise exceed payment of {payment_amount} paise"
        )
    return total
