"""Configuration.

Two layers, deliberately separated:

``FinancialConfig``
    A frozen, plain dataclass holding every number that changes a financial
    outcome. The pure calculators take it as an explicit argument, so a test can
    reconcile under a different tolerance or fee schedule without touching
    environment variables or global state.

``Settings``
    Environment loading (secrets, URLs, defaults). Builds a ``FinancialConfig``.

Every reconciliation run persists ``FinancialConfig.snapshot()`` into
``recon.recon_runs.config_snapshot`` so that a historical result can always be
explained by the parameters it was actually produced under.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.enums import PaymentMethod

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# SYNTHETIC MODEL DEFAULTS. These are *not* asserted Razorpay pricing - see
# docs/ASSUMPTIONS.md. Phase 11 verifies them against official documentation.
DEFAULT_FEE_SCHEDULE_BPS: Mapping[PaymentMethod, int] = MappingProxyType(
    {
        PaymentMethod.CARD: 200,
        PaymentMethod.NETBANKING: 190,
        PaymentMethod.UPI: 0,
        PaymentMethod.WALLET: 200,
    }
)

DEFAULT_GST_RATE_BPS = 1800  # 18%


@dataclass(frozen=True, slots=True)
class FinancialConfig:
    """Every parameter that can change a financial outcome."""

    #: Absolute paise difference still considered a match. Default 1 paise
    #: (Rs 0.01) to absorb a single legitimate GST rounding step.
    tolerance_paise: int = 1

    #: GST on the processing fee, in basis points.
    gst_rate_bps: int = DEFAULT_GST_RATE_BPS

    #: Processing fee rate per payment method, in basis points.
    fee_schedule_bps: Mapping[PaymentMethod, int] = field(
        default_factory=lambda: DEFAULT_FEE_SCHEDULE_BPS
    )

    #: Whether the processing fee and its GST are credited back when a payment
    #: is refunded. Phase 1 decision: False (fee is retained by the gateway).
    #: Flipping this changes the expected settlement of every refunded payment.
    reverse_fee_on_refund: bool = False

    #: Business days from capture until a payment becomes settlement-eligible.
    settlement_cycle_days: int = 2

    #: Extra business days allowed before a late settlement is called missing.
    settlement_grace_days: int = 1

    #: Evidence-score thresholds (Phase 9 consumes these).
    evidence_auto_resolve: int = 90
    evidence_review_min: int = 60

    def fee_rate_bps(self, method: PaymentMethod) -> int:
        try:
            return self.fee_schedule_bps[PaymentMethod(method)]
        except (KeyError, ValueError) as exc:
            raise ValueError(f"no fee rate configured for method {method!r}") from exc

    def snapshot(self) -> dict:
        """JSON-serialisable record of the parameters a run used."""
        return {
            "tolerance_paise": self.tolerance_paise,
            "gst_rate_bps": self.gst_rate_bps,
            "fee_schedule_bps": {str(k): v for k, v in self.fee_schedule_bps.items()},
            "reverse_fee_on_refund": self.reverse_fee_on_refund,
            "settlement_cycle_days": self.settlement_cycle_days,
            "settlement_grace_days": self.settlement_grace_days,
            "evidence_auto_resolve": self.evidence_auto_resolve,
            "evidence_review_min": self.evidence_review_min,
        }

    def snapshot_json(self) -> str:
        return json.dumps(self.snapshot(), sort_keys=True)


class Settings(BaseSettings):
    """Environment-loaded settings. Secrets never leave the server side."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    database_url: str = (
        "postgresql+psycopg://sd_owner:sd_owner@localhost:55432/settlement_detective"
    )
    agent_database_url: str = ""
    eval_database_url: str = ""
    sd_agent_password: str = ""
    sd_eval_password: str = ""

    tolerance_paise: int = 1
    gst_rate_bps: int = DEFAULT_GST_RATE_BPS
    reverse_fee_on_refund: bool = False
    settlement_cycle_days: int = 2
    settlement_grace_days: int = 1
    as_of_date: date = date(2026, 1, 31)

    fee_rate_bps_card: int = 200
    fee_rate_bps_netbanking: int = 190
    fee_rate_bps_upi: int = 0
    fee_rate_bps_wallet: int = 200

    evidence_auto_resolve: int = 90
    evidence_review_min: int = 60

    def financial(self) -> FinancialConfig:
        return FinancialConfig(
            tolerance_paise=self.tolerance_paise,
            gst_rate_bps=self.gst_rate_bps,
            fee_schedule_bps=MappingProxyType(
                {
                    PaymentMethod.CARD: self.fee_rate_bps_card,
                    PaymentMethod.NETBANKING: self.fee_rate_bps_netbanking,
                    PaymentMethod.UPI: self.fee_rate_bps_upi,
                    PaymentMethod.WALLET: self.fee_rate_bps_wallet,
                }
            ),
            reverse_fee_on_refund=self.reverse_fee_on_refund,
            settlement_cycle_days=self.settlement_cycle_days,
            settlement_grace_days=self.settlement_grace_days,
            evidence_auto_resolve=self.evidence_auto_resolve,
            evidence_review_min=self.evidence_review_min,
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
