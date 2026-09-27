from __future__ import annotations
import json, os, time, random, math
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any
from urllib import request, error
from .env_loader import require_api_key, load_dotenv

BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_HTTP_RETRIES = 8
RETRYABLE_HTTP_CODES = {408, 409, 429, 500, 502, 503, 504}


def retry_after_seconds(value: str | None) -> float | None:
    """Parse both HTTP Retry-After formats; ignore malformed headers."""
    if value is None:
        return None
    try:
        seconds = float(value)
    except (ValueError, TypeError):
        try:
            seconds = parsedate_to_datetime(value).timestamp() - time.time()
        except (ValueError, TypeError, OverflowError):
            return None
    return max(0.0, seconds) if math.isfinite(seconds) else None


class OpenRouterError(RuntimeError):
    pass

class BillingUnknown(OpenRouterError):
    """A response was received but its billing information cannot be decoded."""
    pass

class BudgetStopped(OpenRouterError):
    pass


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int = 0
    cached_tokens: int = 0
    cost: float = 0.0
    unknown_cost_calls: int = 0

    @classmethod
    def from_response(cls, payload: dict[str, Any]) -> "Usage":
        def mapping(value): return value if isinstance(value,dict) else {}
        def count(value):
            try: return max(0,int(value)) if not isinstance(value,bool) else 0
            except (ValueError,TypeError,OverflowError): return 0
        u = mapping(mapping(payload).get("usage"))
        ctd = mapping(u.get("completion_tokens_details"))
        ptd = mapping(u.get("prompt_tokens_details"))
        cost=u.get('cost')
        known=type(cost) in (int,float) and math.isfinite(cost) and cost >= 0
        return cls(
            prompt_tokens=count(u.get("prompt_tokens")),
            completion_tokens=count(u.get("completion_tokens")),
            total_tokens=count(u.get("total_tokens")),
            reasoning_tokens=count(ctd.get("reasoning_tokens")),
            cached_tokens=count(ptd.get("cached_tokens")),
            cost=float(cost) if known else 0.0,
            unknown_cost_calls=0 if known else 1,
        )

    def add(self, other: "Usage") -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.total_tokens += other.total_tokens
        self.reasoning_tokens += other.reasoning_tokens
        self.cached_tokens += other.cached_tokens
        self.cost += other.cost
        self.unknown_cost_calls += other.unknown_cost_calls

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class OpenRouterClient:
    def __init__(self, api_key: str | None = None, timeout: int = 180, retries: int = DEFAULT_HTTP_RETRIES):
        if type(retries) is not int or retries < 0:
            raise ValueError('HTTP retries must be a nonnegative integer')
        load_dotenv()
        self.api_key = api_key or require_api_key()
        self.timeout = timeout
        self.retries = retries
        self.before_attempt = None
        self.http_referer = os.environ.get("OPENROUTER_HTTP_REFERER", "")
        self.x_title = os.environ.get("OPENROUTER_X_TITLE", "DGF-Bench")
        self._stats = {"http_attempts": 0, "retries": 0, "rate_limits": 0, "transient_http_errors": 0, "network_errors": 0}

    def _headers(self, auth: bool = True) -> dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if auth:
            h["Authorization"] = f"Bearer {self.api_key}"
        if self.http_referer:
            h["HTTP-Referer"] = self.http_referer
        if self.x_title:
            h["X-Title"] = self.x_title
        return h

    def _json_request(self, method: str, path: str, body: dict | None = None, auth: bool = True) -> dict:
        url = BASE_URL + path
        data = None if body is None else json.dumps(body).encode("utf-8")
        last = None
        for attempt in range(self.retries + 1):
            if self.before_attempt is not None:
                self.before_attempt()
            retry_after = None
            self._stats["http_attempts"] += 1
            if attempt > 0:
                self._stats["retries"] += 1
            req = request.Request(url, data=data, headers=self._headers(auth), method=method)
            try:
                with request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read().decode("utf-8")
                    try: payload=json.loads(raw)
                    except json.JSONDecodeError as exc: raise BillingUnknown('Provider returned invalid JSON; billing status unknown') from exc
                    if not isinstance(payload,dict):
                        raise BillingUnknown('Provider returned a non-object JSON response; billing status unknown')
                    return payload
            except error.HTTPError as e:
                raw = e.read().decode("utf-8", errors="replace")
                last = f"HTTP {e.code}: {raw[:2000]}"
                if e.code == 429:
                    self._stats["rate_limits"] += 1
                elif e.code in RETRYABLE_HTTP_CODES:
                    self._stats["transient_http_errors"] += 1
                if e.code not in RETRYABLE_HTTP_CODES or attempt >= self.retries:
                    raise OpenRouterError(last) from e
                retry_after = retry_after_seconds(e.headers.get('Retry-After') if e.headers else None)
                reason = f'HTTP {e.code}'
            except (error.URLError, TimeoutError) as e:
                self._stats["network_errors"] += 1
                last = str(e)
                if attempt >= self.retries:
                    raise OpenRouterError(last) from e
                reason = type(e).__name__
            delay = min(60.0, 2.0 * (2 ** min(attempt, 6))) + random.random()
            if retry_after is not None:
                # Do not retry earlier than requested, or hold a worker indefinitely.
                if retry_after > 300:
                    raise OpenRouterError(f'{reason}: Retry-After exceeds 300 seconds; request stopped')
                delay = max(delay, retry_after)
            label = (body or {}).get('model', path)
            print(f'[http retry {attempt + 1}/{self.retries} | {label}] '
                  f'{reason}; waiting {delay:.1f}s', flush=True)
            # Another worker can exhaust the shared budget while this one waits.
            remaining = delay
            while remaining > 0:
                if self.before_attempt is not None:
                    self.before_attempt()
                step = min(1.0, remaining)
                time.sleep(step)
                remaining -= step
        raise OpenRouterError(last or "OpenRouter request failed")

    def stats_snapshot(self) -> dict[str, int]:
        return dict(self._stats)

    def list_models(self) -> list[dict[str, Any]]:
        payload = self._json_request("GET", "/models", None, auth=True)
        return list(payload.get("data") or [])

    def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload = dict(payload)
        payload.setdefault("usage", {"include": True})
        return self._json_request("POST", "/chat/completions", payload, auth=True)
