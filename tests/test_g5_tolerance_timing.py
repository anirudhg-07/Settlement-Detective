"""G5 - tolerance boundaries and settlement timing.

The timing rules are what stop the reconciler flagging every recent payment as
missing. Without them the exception rate is meaningless.
"""

from __future__ import annotations

import ast
import pathlib
from datetime import date, datetime, timezone

import pytest

from backend.config import FinancialConfig
from backend.enums import PaymentMethod, PaymentStatus, ReconStatus
from backend.reconciliation.settlement_math import (
    PaymentFacts,
    expected_net_settlement,
    reconcile_payment,
)
from backend.reconciliation.timing import (
    add_business_days,
    is_late_settlement,
    settlement_deadline,
    settlement_eligible_on,
)

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent

CAPTURED = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)  # a Monday


def _expected(cfg, **overrides):
    base = dict(
        payment_id="pay_1001",
        amount=100_000,
        method=PaymentMethod.CARD,
        status=PaymentStatus.CAPTURED,
        captured_at=CAPTURED,
        fee=2_000,
        tax=360,
    )
    base.update(overrides)
    return expected_net_settlement(PaymentFacts(**base), cfg)


# --- 21. both sides of the tolerance boundary -----------------------------
@pytest.mark.parametrize(
    "delta,expected_status",
    [
        (0, ReconStatus.MATCHED),
        (1, ReconStatus.MATCHED),  # exactly at tolerance
        (-1, ReconStatus.MATCHED),
        (2, ReconStatus.EXCEPTION),  # one paise past it
        (-2, ReconStatus.EXCEPTION),
    ],
)
def test_tolerance_boundary_is_inclusive(delta, expected_status, cfg):
    expected = _expected(cfg)
    outcome = reconcile_payment(
        expected,
        actual_net=expected.expected_net + delta,
        has_settled_items=True,
        captured_at=CAPTURED,
        as_of=date(2026, 1, 31),
        cfg=cfg,
    )
    assert outcome.status is expected_status
    assert outcome.delta == delta


# --- 22. a clean transaction matches even at zero tolerance ---------------
def test_clean_transaction_matches_with_zero_tolerance(cfg_zero_tolerance):
    """Tolerance must not be papering over systematic rounding drift."""
    expected = _expected(cfg_zero_tolerance)
    outcome = reconcile_payment(
        expected,
        actual_net=97_640,
        has_settled_items=True,
        captured_at=CAPTURED,
        as_of=date(2026, 1, 31),
        cfg=cfg_zero_tolerance,
    )
    assert outcome.status is ReconStatus.MATCHED
    assert outcome.delta == 0


# --- 23 & 24. pending vs missing ------------------------------------------
def test_unsettled_inside_the_window_is_pending_not_an_exception(cfg):
    """Captured Mon 5 Jan -> eligible Wed 7 -> deadline Thu 8."""
    expected = _expected(cfg)
    outcome = reconcile_payment(
        expected,
        actual_net=0,
        has_settled_items=False,
        captured_at=CAPTURED,
        as_of=date(2026, 1, 7),
        cfg=cfg,
    )
    assert outcome.status is ReconStatus.PENDING_SETTLEMENT
    assert not outcome.is_exception


def test_unsettled_past_the_window_is_an_exception(cfg):
    expected = _expected(cfg)
    outcome = reconcile_payment(
        expected,
        actual_net=0,
        has_settled_items=False,
        captured_at=CAPTURED,
        as_of=date(2026, 1, 9),
        cfg=cfg,
    )
    assert outcome.status is ReconStatus.EXCEPTION
    assert outcome.delta == -97_640


def test_nothing_owed_and_nothing_settled_is_a_match(cfg):
    """A failed payment is reconciled, not left pending forever."""
    expected = _expected(cfg, status=PaymentStatus.FAILED)
    outcome = reconcile_payment(
        expected,
        actual_net=0,
        has_settled_items=False,
        captured_at=CAPTURED,
        as_of=date(2026, 1, 31),
        cfg=cfg,
    )
    assert outcome.status is ReconStatus.MATCHED


def test_business_days_skip_weekends():
    friday = date(2026, 1, 30)
    assert add_business_days(friday, 1) == date(2026, 2, 2)  # Monday
    assert settlement_eligible_on(friday, FinancialConfig()) == date(2026, 2, 3)
    assert settlement_deadline(friday, FinancialConfig()) == date(2026, 2, 4)


def test_late_settlement_detection(cfg):
    assert not is_late_settlement(CAPTURED, date(2026, 1, 8), cfg)
    assert is_late_settlement(CAPTURED, date(2026, 1, 9), cfg)


# --- 25. reproducibility: nothing reads the wall clock --------------------
CLOCK_FREE_MODULES = [
    "backend/money.py",
    "backend/reconciliation/fees.py",
    "backend/reconciliation/settlement_math.py",
    "backend/reconciliation/timing.py",
    "backend/reconciliation/guards.py",
]


def test_reconciliation_never_reads_the_wall_clock():
    """`as_of_date` must fully determine the output.

    If any of these modules called `datetime.now()`, the same dataset would
    reconcile differently tomorrow and the evaluation would be irreproducible.
    """
    offences: list[str] = []
    for rel in CLOCK_FREE_MODULES:
        path = PROJECT_ROOT / rel
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call):
                src = ast.unparse(node.func)
                if src in {"datetime.now", "datetime.utcnow", "date.today", "time.time"}:
                    offences.append(f"{rel}:{node.lineno} calls {src}()")
    assert offences == [], "wall-clock read in reconciliation code:\n" + "\n".join(
        offences
    )


def test_same_inputs_two_runs_give_identical_results(cfg):
    expected = _expected(cfg)
    kwargs = dict(
        actual_net=97_000,
        has_settled_items=True,
        captured_at=CAPTURED,
        as_of=date(2026, 1, 31),
        cfg=cfg,
    )
    assert reconcile_payment(expected, **kwargs) == reconcile_payment(expected, **kwargs)
