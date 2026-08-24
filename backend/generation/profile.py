"""FreshKart's merchant profile.

The distributions here are what make the dataset defensible: an online grocer's
basket sizes, an Indian e-commerce payment-method mix (UPI-dominant), and
refund and failure rates in plausible ranges. A uniformly random dataset would
produce meaningless reconciliation statistics.

Every rate is expressed in parts-per-ten-thousand so that no probability is a
float - consistent with the money layer's discipline, and it keeps the profile
exactly reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from backend.enums import AdjustmentType, PaymentMethod

#: Basket-size bands in whole rupees, with weights. An online grocer's
#: distribution: dense in the few-hundreds, a long thin tail of bulk orders.
AMOUNT_BANDS: Sequence[tuple[int, int, int]] = (
    (100, 499, 30),
    (500, 999, 27),
    (1_000, 2_499, 25),
    (2_500, 4_999, 12),
    (5_000, 14_999, 5),
    (15_000, 49_999, 1),
)

#: UPI-dominant, as Indian e-commerce is.
METHOD_WEIGHTS: dict[PaymentMethod, int] = {
    PaymentMethod.UPI: 55,
    PaymentMethod.CARD: 22,
    PaymentMethod.WALLET: 15,
    PaymentMethod.NETBANKING: 8,
}

CUSTOMER_TYPE_WEIGHTS: dict[str, int] = {"retail": 92, "business": 8}

ADJUSTMENT_TYPE_WEIGHTS: dict[AdjustmentType, int] = {
    AdjustmentType.CHARGEBACK: 30,
    AdjustmentType.CHARGEBACK_REVERSAL: 15,
    AdjustmentType.PLATFORM_FEE: 25,
    AdjustmentType.MANUAL_CREDIT: 15,
    AdjustmentType.MANUAL_DEBIT: 15,
}


@dataclass(frozen=True, slots=True)
class MerchantProfile:
    """Rates in basis points (10_000 = 100%), so nothing here is a float."""

    name: str = "FreshKart"

    #: Payments that never capture - expired UPI collect requests, declined cards.
    payment_failure_bps: int = 400  # 4%

    #: Captured payments that see a refund. Grocery: damaged goods, missing items.
    refund_bps: int = 900  # 9%
    #: Of refunded payments, how many are refunded in full rather than in part.
    full_refund_share_bps: int = 4_000  # 40%
    #: Partial refunds as a share of the payment, sampled between these bounds.
    partial_refund_min_bps: int = 1_000  # 10%
    partial_refund_max_bps: int = 8_000  # 80%

    #: Captured payments carrying a settlement adjustment.
    adjustment_bps: int = 120  # 1.2%

    #: Repeat business: roughly this many payments per customer.
    payments_per_customer: int = 4

    #: Days of history to generate, ending at the as-of date.
    history_days: int = 90

    #: Days between capture and a refund being requested.
    refund_delay_days: tuple[int, int] = (1, 14)
    #: Days between a refund being requested and processed.
    refund_processing_days: tuple[int, int] = (0, 2)

    amount_bands: Sequence[tuple[int, int, int]] = field(default_factory=lambda: AMOUNT_BANDS)
    method_weights: dict = field(default_factory=lambda: dict(METHOD_WEIGHTS))
    customer_type_weights: dict = field(default_factory=lambda: dict(CUSTOMER_TYPE_WEIGHTS))
    adjustment_type_weights: dict = field(default_factory=lambda: dict(ADJUSTMENT_TYPE_WEIGHTS))
