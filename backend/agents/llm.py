"""LLM provider adapter - pluggable, cached, and self-throttling.

Three concerns live here, and all three exist because the project runs on a
free API key with a hard ceiling of 500 requests a day:

* **Pluggable.** The provider is a config value. Swapping Gemini for another
  model is a `.env` edit, not a rewrite.
* **Cached.** Every raw response is written to disk keyed by an exact hash of
  the request. Re-scoring, re-analysis and dashboard work replay from disk for
  free - most wasted quota is not spent on bugs, it is spent re-fetching
  answers you already have.
* **Throttled.** The client paces itself under the provider's requests-per-
  minute limit rather than discovering it via 429s.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx

from backend.config import PROJECT_ROOT, Settings

CACHE_DIR = PROJECT_ROOT / "data" / "llm_cache"


class LLMError(RuntimeError):
    """The model could not be reached or returned something unusable."""


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0
    from_cache: bool = False
    #: Provider-native model turn, echoed back verbatim on the next request.
    #: Gemini 3.x attaches thought signatures to parts; re-serialising them
    #: from our own shape would drop information the model expects back.
    raw_model_turn: Any = None


class LLMProvider(Protocol):
    def send(
        self, *, system: str, history: list[dict], tools: list[dict]
    ) -> LLMResponse: ...


# --------------------------------------------------------------------------
# Rate limiting
# --------------------------------------------------------------------------


class RateLimiter:
    """Spaces requests so the free-tier per-minute ceiling is never hit."""

    def __init__(self, rpm: int) -> None:
        self._min_interval = 60.0 / max(1, rpm)
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> float:
        with self._lock:
            gap = time.monotonic() - self._last
            delay = max(0.0, self._min_interval - gap)
            if delay:
                time.sleep(delay)
            self._last = time.monotonic()
        return delay


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------


class ResponseCache:
    """Content-addressed cache of raw provider responses."""

    def __init__(self, directory: Path = CACHE_DIR, enabled: bool = True) -> None:
        self.dir = directory
        self.enabled = enabled
        self.hits = 0
        self.misses = 0
        if enabled:
            self.dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def key(model: str, payload: dict) -> str:
        blob = json.dumps({"model": model, "payload": payload}, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:32]

    def get(self, key: str) -> dict | None:
        if not self.enabled:
            return None
        path = self.dir / f"{key}.json"
        if not path.exists():
            self.misses += 1
            return None
        self.hits += 1
        return json.loads(path.read_text())

    def put(self, key: str, value: dict) -> None:
        if self.enabled:
            (self.dir / f"{key}.json").write_text(json.dumps(value))


# --------------------------------------------------------------------------
# Gemini
# --------------------------------------------------------------------------

GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)


def build_http_client() -> httpx.Client:
    """An HTTP client that survives a broken IPv6 path.

    Two deliberate choices:

    * **Forced IPv4.** Binding the local address to ``0.0.0.0`` makes the
      client use IPv4 only. Networks that advertise IPv6 without a working
      route to it leave the connection wedged in ``SYN_SENT`` - the request
      hangs until timeout with no error to explain why. ``curl`` hides this
      by racing both families (Happy Eyeballs); httpx does not.
    * **A short connect timeout, a long read timeout.** Reaching the server
      should take a second; the model thinking about a hard case can take a
      minute. One combined timeout cannot express both.
    """
    return httpx.Client(
        transport=httpx.HTTPTransport(local_address="0.0.0.0", retries=2),
        timeout=httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0),
    )


class GeminiProvider:
    """Gemini via the REST API.

    ``httpx`` rather than ``urllib``: the stdlib client uses the system CA
    store, which on a framework Python install on macOS is empty, so every
    HTTPS call fails with a certificate error that points nowhere near the
    real cause.
    """

    def __init__(self, settings: Settings, cache: ResponseCache | None = None) -> None:
        if not settings.llm_api_key:
            raise LLMError("LLM_API_KEY is not set in .env")
        self.model = settings.llm_model
        self.api_key = settings.llm_api_key
        self.cache = cache or ResponseCache(enabled=settings.llm_cache_enabled)
        self.limiter = RateLimiter(settings.llm_rpm)
        self.client = build_http_client()
        self.requests_made = 0
        self.throttled_seconds = 0.0

    def send(
        self, *, system: str, history: list[dict], tools: list[dict]
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": history,
            "generationConfig": {"temperature": 0},
        }
        if tools:
            payload["tools"] = [{"functionDeclarations": tools}]

        key = self.cache.key(self.model, payload)
        cached = self.cache.get(key)
        if cached is not None:
            return self._parse(cached, from_cache=True)

        self.throttled_seconds += self.limiter.wait()
        try:
            response = self.client.post(
                GEMINI_ENDPOINT.format(model=self.model),
                headers={"x-goog-api-key": self.api_key},
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise LLMError(f"network failure calling {self.model}: {exc}") from exc

        self.requests_made += 1
        if response.status_code != 200:
            raise LLMError(
                f"HTTP {response.status_code} from {self.model}: {response.text[:300]}"
            )

        data = response.json()
        if "error" in data:
            raise LLMError(f"{data['error'].get('status')}: {data['error'].get('message')}")
        self.cache.put(key, data)
        return self._parse(data, from_cache=False)

    @staticmethod
    def _parse(data: dict, *, from_cache: bool) -> LLMResponse:
        candidates = data.get("candidates") or []
        if not candidates:
            raise LLMError(f"no candidates in response: {json.dumps(data)[:300]}")
        content = candidates[0].get("content", {})
        parts = content.get("parts", []) or []

        calls = tuple(
            ToolCall(name=p["functionCall"]["name"], args=p["functionCall"].get("args") or {})
            for p in parts
            if "functionCall" in p
        )
        text = "".join(p.get("text", "") for p in parts)
        usage = data.get("usageMetadata", {})
        return LLMResponse(
            text=text,
            tool_calls=calls,
            input_tokens=usage.get("promptTokenCount", 0) or 0,
            output_tokens=usage.get("candidatesTokenCount", 0) or 0,
            from_cache=from_cache,
            raw_model_turn={"role": "model", "parts": parts},
        )

    # -- provider-native message construction -----------------------------

    @staticmethod
    def user_turn(text: str) -> dict:
        return {"role": "user", "parts": [{"text": text}]}

    @staticmethod
    def tool_result_turn(results: list[tuple[str, dict]]) -> dict:
        """Tool outputs go back as a user turn carrying functionResponse parts."""
        return {
            "role": "user",
            "parts": [
                {"functionResponse": {"name": name, "response": payload}}
                for name, payload in results
            ],
        }


def build_provider(settings: Settings, cache: ResponseCache | None = None):
    provider = (settings.llm_provider or "").lower()
    if provider == "gemini":
        return GeminiProvider(settings, cache=cache)
    raise LLMError(
        f"unsupported LLM_PROVIDER {settings.llm_provider!r}; supported: gemini"
    )
