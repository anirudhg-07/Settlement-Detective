from __future__ import annotations

from types import MappingProxyType

import pytest

from backend.config import FinancialConfig
from backend.enums import PaymentMethod


@pytest.fixture
def cfg() -> FinancialConfig:
    """Default financial configuration: fee retained on refund, 1 paise tolerance."""
    return FinancialConfig()


@pytest.fixture
def cfg_reversing() -> FinancialConfig:
    """The alternative Phase 1 decision: fee and GST credited back on refund."""
    return FinancialConfig(reverse_fee_on_refund=True)


@pytest.fixture
def cfg_zero_tolerance() -> FinancialConfig:
    return FinancialConfig(tolerance_paise=0)


@pytest.fixture
def cfg_flat_fee() -> FinancialConfig:
    """Every method at 2% - keeps method out of the picture when it is noise."""
    return FinancialConfig(
        fee_schedule_bps=MappingProxyType({m: 200 for m in PaymentMethod})
    )
