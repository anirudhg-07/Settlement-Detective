"""Money primitives.

Every monetary value in this system is an ``int`` count of **paise**.
Rupees exist only at the display boundary and at ingest.

Why integers and not ``Decimal``:
  * addition is exact and associative, so the batch invariant
    ``sum(item.net_amount) == settlement.net_amount`` is *provable*, not
    approximate;
  * rounding then happens at exactly one place in the codebase - the fee/tax
    calculator - instead of being re-decided at every call site.

``float`` is rejected at every entry point. A float can neither represent
``0.10`` exactly nor survive summation, and in reconciliation the whole product
claim rests on residuals being genuinely zero.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

#: Number of paise in one rupee.
PAISE_PER_RUPEE = 100

_MAX_MINOR_DIGITS = 2
_ONE = Decimal(1)


class MoneyError(ValueError):
    """Raised when a value cannot be represented exactly as paise."""


def rupees_to_paise(value: str | int | Decimal) -> int:
    """Convert a rupee amount to exact paise.

    Accepts ``str`` (preferred - lossless), ``int``, or ``Decimal``.
    Rejects ``float`` and anything with sub-paise precision, rather than
    silently rounding at the parse boundary where the loss would be invisible.

    >>> rupees_to_paise("976.40")
    97640
    """
    if isinstance(value, bool):
        raise MoneyError("bool is not a monetary value")
    if isinstance(value, float):
        raise MoneyError(
            f"float is not accepted as money ({value!r}); "
            "pass a str or Decimal to avoid binary rounding loss"
        )
    if isinstance(value, int):
        return value * PAISE_PER_RUPEE
    if isinstance(value, str):
        try:
            dec = Decimal(value.strip())
        except InvalidOperation as exc:
            raise MoneyError(f"not a valid rupee amount: {value!r}") from exc
    elif isinstance(value, Decimal):
        dec = value
    else:
        raise MoneyError(f"unsupported money type: {type(value).__name__}")

    if not dec.is_finite():
        raise MoneyError(f"non-finite rupee amount: {value!r}")

    exponent = dec.as_tuple().exponent
    if isinstance(exponent, int) and -exponent > _MAX_MINOR_DIGITS:
        raise MoneyError(
            f"sub-paise precision is not representable: {value!r} "
            f"has {-exponent} decimal places, maximum is {_MAX_MINOR_DIGITS}"
        )
    return int(dec.scaleb(_MAX_MINOR_DIGITS))


def paise_to_rupees(paise: int) -> Decimal:
    """Convert paise to an exact ``Decimal`` rupee amount with 2 places."""
    _require_int(paise)
    return (Decimal(paise) / Decimal(PAISE_PER_RUPEE)).quantize(Decimal("0.01"))


def format_paise(paise: int, symbol: str = "₹") -> str:
    """Human-readable rupee string, e.g. ``₹976.40``. Display only."""
    _require_int(paise)
    sign = "-" if paise < 0 else ""
    return f"{sign}{symbol}{abs(paise) // PAISE_PER_RUPEE}.{abs(paise) % PAISE_PER_RUPEE:02d}"


def round_half_up(value: Decimal) -> int:
    """Round an exact ``Decimal`` paise quantity to a whole paise, half away from zero.

    This is the **only** rounding site in the financial model. Uses
    ``ROUND_HALF_UP`` (not banker's rounding) because that is what invoices and
    tax computations conventionally use, and consistency with the counterparty
    matters more here than statistical neutrality.
    """
    if not isinstance(value, Decimal):
        raise MoneyError(
            f"round_half_up expects Decimal, got {type(value).__name__}"
        )
    if not value.is_finite():
        raise MoneyError("cannot round a non-finite value")
    # ROUND_HALF_UP in Python's decimal rounds half *away from zero*, which is
    # what we want symmetrically for credits and debits.
    return int(value.quantize(_ONE, rounding="ROUND_HALF_UP"))


def apply_bps(amount_paise: int, rate_bps: int) -> int:
    """Apply a basis-point rate to a paise amount, rounded to whole paise.

    Rates are held as integer basis points (100 bps = 1.00%) so that no rate is
    ever a float. 2.00% of ₹1,000.00 -> ``apply_bps(100_000, 200) == 2_000``.
    """
    _require_int(amount_paise)
    _require_int(rate_bps)
    if rate_bps < 0:
        raise MoneyError(f"rate_bps must be non-negative, got {rate_bps}")
    return round_half_up(Decimal(amount_paise) * Decimal(rate_bps) / Decimal(10_000))


def _require_int(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MoneyError(
            f"expected int paise, got {type(value).__name__}: {value!r}"
        )
