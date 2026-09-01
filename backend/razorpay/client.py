"""Razorpay Settlements API client.

Only three endpoints appear here, and each one was read from the current
official documentation before it was written. Nothing is inferred from another
endpoint's shape, and no path, parameter or response field is invented - that
is the rule this project holds itself to for any external API (spec §43).

Verified against, on 2026-08-29:
    https://razorpay.com/docs/api/settlements/
    https://razorpay.com/docs/api/settlements/fetch-recon/
    https://razorpay.com/docs/api/settlements/entity/

    GET /v1/settlements                      list settlements
    GET /v1/settlements/{id}                 one settlement
    GET /v1/settlements/recon/combined       the recon report (year, month
                                             required; day, count, skip optional)

Test Mode only. The secret key never leaves the server and is never logged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from backend.agents.llm import build_http_client
from backend.config import Settings

BASE_URL = "https://api.razorpay.com/v1"

#: Settlement statuses, per the Settlement entity documentation.
SETTLEMENT_STATUSES = frozenset({"created", "processed", "failed"})

#: Recon row types, per the recon report documentation.
RECON_TYPES = frozenset({"payment", "refund", "transfer", "adjustment"})


class RazorpayError(RuntimeError):
    """The API could not be reached, or refused the request."""


@dataclass
class RazorpayCredentials:
    key_id: str
    key_secret: str

    @property
    def is_test_mode(self) -> bool:
        """Test keys are prefixed `rzp_test_`; live keys `rzp_live_`.

        Checked so a live key cannot be used by accident - this project has no
        business touching real settlement data.
        """
        return self.key_id.startswith("rzp_test_")

    def redacted(self) -> str:
        return f"{self.key_id[:12]}…" if self.key_id else "<unset>"


class RazorpayClient:
    def __init__(self, credentials: RazorpayCredentials,
                 client: httpx.Client | None = None) -> None:
        if not credentials.key_id or not credentials.key_secret:
            raise RazorpayError(
                "RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET are not set in .env"
            )
        if not credentials.is_test_mode:
            raise RazorpayError(
                f"refusing to use a non-test key ({credentials.redacted()}). "
                "This project reads Test Mode only."
            )
        self.credentials = credentials
        # Same transport as the LLM client: forced IPv4, split connect/read
        # timeouts. A network that advertises IPv6 without routing it leaves
        # requests wedged in SYN_SENT with no error to explain why.
        self.client = client or build_http_client()

    def _get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        try:
            response = self.client.get(
                f"{BASE_URL}{path}",
                params=params or {},
                auth=(self.credentials.key_id, self.credentials.key_secret),
            )
        except httpx.HTTPError as exc:
            raise RazorpayError(f"network failure calling {path}: {exc}") from exc

        if response.status_code == 401:
            raise RazorpayError(
                "Razorpay rejected the credentials (401). Check RAZORPAY_KEY_ID "
                "and RAZORPAY_KEY_SECRET are a matching Test Mode pair."
            )
        if response.status_code != 200:
            raise RazorpayError(
                f"HTTP {response.status_code} from {path}: {response.text[:300]}"
            )
        return response.json()

    # -- the three verified endpoints -------------------------------------

    def list_settlements(self, *, count: int = 100, skip: int = 0) -> list[dict]:
        """GET /v1/settlements — the settlement batches on this account."""
        return self._get("/settlements", {"count": count, "skip": skip}).get("items", [])

    def get_settlement(self, settlement_id: str) -> dict:
        """GET /v1/settlements/{id} — one batch."""
        return self._get(f"/settlements/{settlement_id}")

    def recon_report(
        self, *, year: int, month: int, day: int | None = None,
        count: int = 100, skip: int = 0,
    ) -> list[dict]:
        """GET /v1/settlements/recon/combined — the line items of a settlement.

        This is the endpoint that matters: one row per settled payment, refund,
        transfer or adjustment, carrying `entity_id`, `type`, `debit`, `credit`,
        `fee`, `tax` and `settlement_id`. It is the same shape as our
        `settlement_items` table, which is not a coincidence - the model was
        built to match how settlement actually works.
        """
        params: dict[str, Any] = {"year": year, "month": month,
                                  "count": count, "skip": skip}
        if day is not None:
            params["day"] = day
        return self._get("/settlements/recon/combined", params).get("items", [])


def build_client(settings: Settings) -> RazorpayClient:
    return RazorpayClient(
        RazorpayCredentials(settings.razorpay_key_id, settings.razorpay_key_secret)
    )
