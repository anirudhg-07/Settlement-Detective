"""Processing fee and GST calculation.

Two rules, and only two:

    fee = round_half_up(amount x fee_rate_bps / 10_000)
    tax = round_half_up(fee    x gst_rate_bps / 10_000)

Tax is computed on the **fee**, never on the payment amount. Getting that wrong
inflates tax by roughly the reciprocal of the fee rate - about 50x at 2% - and
it is the single easiest mistake to make in this model, so it has a dedicated
regression test.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.config import FinancialConfig
from backend.enums import PaymentMethod
from backend.money import apply_bps


@dataclass(frozen=True, slots=True)
class FeeBreakdown:
    """What the gateway keeps out of a payment, in paise."""

    fee: int
    tax: int
    fee_rate_bps: int

    @property
    def total_deduction(self) -> int:
        return self.fee + self.tax


def compute_fee(amount_paise: int, method: PaymentMethod, cfg: FinancialConfig) -> int:
    """Processing fee in paise for a payment of ``amount_paise`` via ``method``."""
    return apply_bps(amount_paise, cfg.fee_rate_bps(method))


def compute_tax(fee_paise: int, cfg: FinancialConfig) -> int:
    """GST in paise on a processing fee. Computed on the fee, not the payment."""
    return apply_bps(fee_paise, cfg.gst_rate_bps)


def compute_fee_and_tax(
    amount_paise: int, method: PaymentMethod, cfg: FinancialConfig
) -> FeeBreakdown:
    rate = cfg.fee_rate_bps(method)
    fee = apply_bps(amount_paise, rate)
    return FeeBreakdown(fee=fee, tax=compute_tax(fee, cfg), fee_rate_bps=rate)
