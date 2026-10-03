"""Effect adapter boundary.

Adapters are the *external-effect boundary*: the only code that talks to
external systems. They receive immutable :class:`EffectView` objects, never
database sessions, and they return typed results. Core services never contain
provider-specific logic; they only call this protocol.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.domain.effect import EffectContract, EffectView
from app.domain.enums import DispatchOutcome, ProviderFinding, VerificationStatus
from app.domain.verification import (
    DispatchResult,
    PrepareResult,
    ReconciliationFinding,
    VerificationResult,
)


def provider_idempotency_key(operation_key: str) -> str:
    """Stable provider idempotency key derived from the *logical* operation.

    Every retry of the same business operation reuses this key - PACT never
    generates a fresh key per attempt.
    """
    return "pact_" + hashlib.sha256(operation_key.encode()).hexdigest()[:32]


@dataclass(frozen=True)
class AdapterContext:
    http: httpx.AsyncClient
    timeout_s: float
    attempt_no: int = 1

    def headers(self, effect: EffectView, idempotency_key: str | None = None) -> dict[str, str]:
        return {
            "Idempotency-Key": idempotency_key or effect.provider_idempotency_key,
            "X-PACT-Transaction-Id": str(effect.transaction_id),
            "X-PACT-Root-Id": str(effect.root_id),
            "X-PACT-Effect-Id": str(effect.id),
            "X-PACT-Operation-Key": effect.operation_key,
        }


class EffectAdapter(Protocol):
    contract: EffectContract

    async def prepare(self, effect: EffectView, ctx: AdapterContext) -> PrepareResult: ...
    async def execute(self, effect: EffectView, ctx: AdapterContext) -> DispatchResult: ...
    async def verify(self, effect: EffectView, ctx: AdapterContext) -> VerificationResult: ...
    async def reconcile(self, effect: EffectView, ctx: AdapterContext) -> ReconciliationFinding: ...
    async def compensate(self, effect: EffectView, ctx: AdapterContext) -> DispatchResult: ...
    async def verify_compensation(self, effect: EffectView, ctx: AdapterContext) -> VerificationResult: ...


class HttpAdapter:
    """Shared HTTP plumbing with honest outcome classification."""

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
            return DispatchResult(
                outcome=DispatchOutcome.RESPONSE_LOST,
                error=f"no response within {ctx.timeout_s}s",
                latency_ms=_ms(started),
            )
        except httpx.ConnectError as exc:
            return DispatchResult(outcome=DispatchOutcome.NOT_SENT, error=str(exc), latency_ms=_ms(started))
        except httpx.HTTPError as exc:
            return DispatchResult(outcome=DispatchOutcome.RESPONSE_LOST, error=str(exc), latency_ms=_ms(started))
        body = _json(resp)
        code = resp.status_code
        if 200 <= code < 300:
            outcome = DispatchOutcome.ACCEPTED
        elif code in (429, 503):
            outcome = DispatchOutcome.REJECTED_RETRYABLE
        elif 400 <= code < 500:
            outcome = DispatchOutcome.REJECTED_DEFINITIVE
        else:  # 5xx other than 503: provider may have applied the change
            outcome = DispatchOutcome.RESPONSE_LOST
        return DispatchResult(
            outcome=outcome,
            http_status=code,
            response=body,
            provider_reference=(body or {}).get("id") if isinstance(body, dict) else None,
            error=(body or {}).get("error") if isinstance(body, dict) and code >= 300 else None,
            latency_ms=_ms(started),
        )

    async def _read(self, ctx: AdapterContext, path: str, params: dict | None = None) -> tuple[int | None, Any]:
        """Read external state. Returns (status, body); status None on transport failure."""
        try:
            resp = await asyncio.wait_for(ctx.http.get(path, params=params), timeout=ctx.timeout_s)
        except (asyncio.TimeoutError, httpx.HTTPError):
            return None, None
        return resp.status_code, _json(resp)

    # Defaults for adapters that do not support an operation.
    async def compensate(self, effect: EffectView, ctx: AdapterContext) -> DispatchResult:
        return DispatchResult(outcome=DispatchOutcome.REJECTED_DEFINITIVE, error="effect is not compensable")

    async def verify_compensation(self, effect: EffectView, ctx: AdapterContext) -> VerificationResult:
        return VerificationResult(status=VerificationStatus.VERIFIED_FAILURE, reason="not compensable")

    async def _target_state_finding(self, verification: VerificationResult) -> ReconciliationFinding:
        if verification.status == VerificationStatus.VERIFIED_SUCCESS:
            return ReconciliationFinding(finding=ProviderFinding.APPLIED, evidence=verification.evidence,
                                         external_reference=verification.external_reference,
                                         reason="target state observed")
        if verification.status == VerificationStatus.VERIFIED_FAILURE:
            return ReconciliationFinding(finding=ProviderFinding.NOT_APPLIED, evidence=verification.evidence,
                                         reason="target state not reached; operation is state-idempotent")
        return ReconciliationFinding(finding=ProviderFinding.INDETERMINATE, evidence=verification.evidence,
                                     reason=verification.reason or "provider state unreadable")


def unreadable(reason: str = "external state unreadable") -> VerificationResult:
    return VerificationResult(status=VerificationStatus.VERIFICATION_UNKNOWN, reason=reason)


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def _json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return {"raw": resp.text[:500]}
