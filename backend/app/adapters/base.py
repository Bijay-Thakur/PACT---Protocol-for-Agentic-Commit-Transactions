"""Effect adapter boundary.

Adapters are the *external-effect boundary*: the only code that talks to
external systems. They receive immutable :class:`EffectView` objects, never
database sessions, and they return typed results. Core services never contain
provider-specific logic; they only call this protocol.

Phase 2 rules enforced here:
- An HTTP 2xx is a *dispatch* outcome (the provider said "ok"), never proof.
- Only statuses the contract declares definitive count as "not applied".
- Only statuses with a contract-level justification are retryable without
  reconciliation; every other ambiguous status becomes UNKNOWN.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

import httpx

from app.domain.effect import EffectContract, EffectView
from app.domain.enums import Application, DispatchOutcome, Postcondition
from app.domain.verification import DispatchResult, Observation, PrepareResult


def provider_idempotency_key(operation_identity: str) -> str:
    """Stable provider idempotency key derived from the *logical* operation identity.

    Every retry of the same business operation reuses this key - PACT never
    generates a fresh key per attempt. 37 chars: within common provider limits.
    """
    return "pact_" + hashlib.sha256(operation_identity.encode()).hexdigest()[:32]


@dataclass(frozen=True)
class AdapterContext:
    http: httpx.AsyncClient
    timeout_s: float
    attempt_no: int = 1
    first_dispatch_at: datetime | None = None
    last_dispatch_at: datetime | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def headers(self, effect: EffectView, idempotency_key: str | None = None,
                if_match: Any = None) -> dict[str, str]:
        h = {
            "Idempotency-Key": idempotency_key or effect.provider_idempotency_key,
            "X-PACT-Transaction-Id": str(effect.transaction_id),
            "X-PACT-Root-Id": str(effect.root_id),
            "X-PACT-Effect-Id": str(effect.id),
            "X-PACT-Operation": effect.operation_key,
        }
        if if_match is not None:
            h["If-Match"] = str(if_match)
        return h


class EffectAdapter(Protocol):
    contract: EffectContract

    async def prepare(self, effect: EffectView, ctx: AdapterContext) -> PrepareResult: ...
    async def freshness(self, effect: EffectView, ctx: AdapterContext) -> tuple[bool, dict[str, Any]]: ...
    async def execute(self, effect: EffectView, ctx: AdapterContext) -> DispatchResult: ...
    async def observe(self, effect: EffectView, ctx: AdapterContext) -> Observation: ...
    async def restore(self, effect: EffectView, ctx: AdapterContext) -> DispatchResult: ...
    async def observe_restoration(self, effect: EffectView, ctx: AdapterContext) -> Observation: ...


class HttpAdapter:
    """Shared HTTP plumbing with contract-driven outcome classification."""

    contract: EffectContract

    async def _send(
        self, ctx: AdapterContext, method: str, path: str, *,
        json: Any = None, headers: dict[str, str] | None = None, params: dict | None = None,
    ) -> DispatchResult:
        started = time.perf_counter()
        try:
            resp = await asyncio.wait_for(
                ctx.http.request(method, path, json=json, headers=headers, params=params),
                timeout=ctx.timeout_s,
            )
        except (asyncio.TimeoutError, httpx.TimeoutException):
            # The request left PACT; the provider may or may not have applied it.
            return DispatchResult(outcome=DispatchOutcome.RESPONSE_LOST,
                                  error=f"no response within {ctx.timeout_s}s", latency_ms=_ms(started))
        except httpx.ConnectError as exc:
            return DispatchResult(outcome=DispatchOutcome.NOT_SENT, error=str(exc), latency_ms=_ms(started))
        except httpx.HTTPError as exc:
            return DispatchResult(outcome=DispatchOutcome.RESPONSE_LOST, error=str(exc), latency_ms=_ms(started))
        body = _json(resp)
        code = resp.status_code
        if 200 <= code < 300:
            outcome = DispatchOutcome.ACCEPTED
        elif code in self.contract.definitive_rejection_statuses:
            outcome = DispatchOutcome.REJECTED_DEFINITIVE
        elif code in self.contract.safe_retry_statuses:
            outcome = DispatchOutcome.REJECTED_RETRYABLE
        else:
            # 5xx, 429, unexpected 4xx: the contract does not let us infer non-application.
            outcome = DispatchOutcome.RESPONSE_LOST
        return DispatchResult(
            outcome=outcome, http_status=code, response=body,
            provider_reference=(body or {}).get("id") if isinstance(body, dict) and code < 300 else None,
            error=(body or {}).get("error") if isinstance(body, dict) and code >= 300 else None,
            latency_ms=_ms(started), stale_precondition=code == 412,
        )

    async def _read(self, ctx: AdapterContext, path: str, params: dict | None = None) -> tuple[int | None, Any]:
        """Read external state. Returns (status, body); status None on transport failure."""
        try:
            resp = await asyncio.wait_for(ctx.http.get(path, params=params), timeout=ctx.timeout_s)
        except (asyncio.TimeoutError, httpx.HTTPError):
            return None, None
        return resp.status_code, _json(resp)

    async def freshness(self, effect: EffectView, ctx: AdapterContext) -> tuple[bool, dict[str, Any]]:
        """Re-read material preconditions captured at prepare; any change makes the plan stale."""
        fresh = await self.prepare(effect, ctx)  # type: ignore[attr-defined]
        before = (effect.prepare_evidence or {}).get("preconditions", {})
        after = fresh.preconditions if fresh.ok else {"prepare_failed": fresh.reason}
        changed = {k: {"frozen": before.get(k), "now": after.get(k)}
                   for k in sorted(set(before) | set(after)) if before.get(k) != after.get(k)}
        return not changed, {"changed": changed}

    # Defaults for adapters that do not support restoration.
    async def restore(self, effect: EffectView, ctx: AdapterContext) -> DispatchResult:
        return DispatchResult(outcome=DispatchOutcome.REJECTED_DEFINITIVE, error="effect is not restorable")

    async def observe_restoration(self, effect: EffectView, ctx: AdapterContext) -> Observation:
        return unreadable("not restorable", source=self.contract.provider)


def unreadable(reason: str = "external state unreadable", *, source: str = "") -> Observation:
    return Observation(application=Application.UNKNOWN, postcondition=Postcondition.UNDETERMINED,
                       readable=False, reason=reason, source=source)


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def _json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return {"raw": resp.text[:500]}
