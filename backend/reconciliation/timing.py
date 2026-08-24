"""Settlement timing.

Distinguishing "not settled yet" from "never settled" is what stops the
reconciler flagging every recent payment as an exception. A payment becomes
settlement-eligible ``settlement_cycle_days`` business days after capture, and
is only called missing once a further ``settlement_grace_days`` have passed.

ASSUMPTION: business days exclude Saturday and Sunday only. No public-holiday
calendar is modelled - a holiday calendar would shift eligibility dates but not
change any amount, and the synthetic generator is holiday-free by construction.
Documented in docs/ASSUMPTIONS.md.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from backend.config import FinancialConfig

_SATURDAY = 5


def is_business_day(day: date) -> bool:
    return day.weekday() < _SATURDAY


def add_business_days(start: date, days: int) -> date:
    """Advance ``start`` by ``days`` business days (weekends skipped)."""
    if days < 0:
        raise ValueError("add_business_days does not support negative offsets")
    current = start
    remaining = days
    while remaining > 0:
        current += timedelta(days=1)
        if is_business_day(current):
            remaining -= 1
    return current


def as_date(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


def settlement_eligible_on(captured_at: date | datetime, cfg: FinancialConfig) -> date:
    """First date on which the payment should appear in a settlement batch."""
    return add_business_days(as_date(captured_at), cfg.settlement_cycle_days)


def settlement_deadline(captured_at: date | datetime, cfg: FinancialConfig) -> date:
    """Last date before an unsettled payment is treated as MISSING_SETTLEMENT."""
    return add_business_days(
        settlement_eligible_on(captured_at, cfg), cfg.settlement_grace_days
    )


def is_late_settlement(
    captured_at: date | datetime, settled_on: date | datetime, cfg: FinancialConfig
) -> bool:
    """True when the money arrived, but after the allowed window."""
    return as_date(settled_on) > settlement_deadline(captured_at, cfg)
