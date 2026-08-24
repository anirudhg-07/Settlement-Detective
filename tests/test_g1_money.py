"""G1 - money primitives.

The whole product claim rests on residuals being genuinely zero, so the money
layer has to refuse anything it cannot represent exactly rather than round it
quietly at the boundary.
"""

from __future__ import annotations

from decimal import Decimal

import ast
import pathlib

import pytest

from backend.money import (
    MoneyError,
    format_paise,
    paise_to_rupees,
    round_half_up,
    rupees_to_paise,
)

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent


# --- 1. exact conversion, exact round-trip --------------------------------
@pytest.mark.parametrize(
    "rupees,paise",
    [
        ("976.40", 97_640),
        ("1000", 100_000),
        ("0.01", 1),
        ("0", 0),
        ("-400.00", -40_000),
        ("99999.99", 9_999_999),
    ],
)
def test_rupees_to_paise_is_exact(rupees, paise):
    assert rupees_to_paise(rupees) == paise


def test_paise_round_trip():
    for paise in (0, 1, 97_640, 100_000, 9_999_999, -40_000):
        assert rupees_to_paise(paise_to_rupees(paise)) == paise


# --- 2. sub-paise precision is refused, never rounded ---------------------
def test_sub_paise_precision_raises():
    with pytest.raises(MoneyError, match="sub-paise"):
        rupees_to_paise("1000.005")


def test_garbage_string_raises():
    with pytest.raises(MoneyError):
        rupees_to_paise("twelve rupees")


# --- 3. float is rejected at the type boundary ----------------------------
def test_float_is_rejected():
    with pytest.raises(MoneyError, match="float"):
        rupees_to_paise(1000.50)


def test_bool_is_rejected():
    with pytest.raises(MoneyError):
        rupees_to_paise(True)


def test_round_half_up_rejects_non_decimal():
    with pytest.raises(MoneyError):
        round_half_up(1)


def test_round_half_up_is_half_away_from_zero():
    assert round_half_up(Decimal("0.5")) == 1
    assert round_half_up(Decimal("1.5")) == 2  # not banker's rounding, which gives 2
    assert round_half_up(Decimal("2.5")) == 3  # banker's rounding would give 2
    assert round_half_up(Decimal("-1.5")) == -2


def test_format_paise():
    assert format_paise(97_640) == "₹976.40"
    assert format_paise(-40_000) == "-₹400.00"
    assert format_paise(5) == "₹0.05"


# --- 4. no float may appear in a financial signature ----------------------
FINANCIAL_MODULES = [
    "backend/money.py",
    "backend/config.py",
    "backend/reconciliation/fees.py",
    "backend/reconciliation/settlement_math.py",
    "backend/reconciliation/timing.py",
    "backend/reconciliation/guards.py",
    "backend/models/base.py",
    "backend/models/ops.py",
    "backend/models/recon.py",
    "backend/models/gt.py",
]


def _float_offences(path: pathlib.Path) -> list[str]:
    """Find float annotations and float literals.

    ``isinstance(x, float)`` guards are deliberately allowed - rejecting floats
    is precisely what the money layer is for.
    """
    tree = ast.parse(path.read_text())
    offences: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            offences.append(f"{path}:{node.lineno} float literal {node.value!r}")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            annotations = [a.annotation for a in node.args.args if a.annotation]
            if node.returns:
                annotations.append(node.returns)
            for ann in annotations:
                if "float" in ast.unparse(ann):
                    offences.append(
                        f"{path}:{node.lineno} float in signature of {node.name}()"
                    )
    return offences


def test_no_floats_in_financial_code():
    """A float anywhere in the money path silently breaks exact reconciliation."""
    offences: list[str] = []
    for rel in FINANCIAL_MODULES:
        offences.extend(_float_offences(PROJECT_ROOT / rel))
    assert offences == [], "float found in financial code:\n" + "\n".join(offences)
