"""Five deterministic simulated external systems behind HTTP APIs.

Billing (Stripe-like), Subscription, Identity/entitlement, CRM, Notification.
Each keeps its own queryable state so PACT can demonstrate that

    execution response != verified external business state

Records are versioned; mutating calls honour ``If-Match: <version>`` and answer
412 when the record changed (compare-and-swap). History entries record which
idempotency key caused each change, so causality can be evidenced.

Fault injection (per system/operation, optionally scoped to one customer):

- ``reject``                    422 before applying (definitive failure)
- ``unavailable``               503 before applying (nothing applied)
- ``apply_then_503``            applies + commits, then answers 503 (503 is NOT proof of non-application)
- ``drop_response_after_apply`` applies + commits, then hangs so the caller times out
- ``timeout_before_apply``      hangs WITHOUT applying, so the caller times out
- ``server_error_after_apply``  applies + commits, then returns 500 (ambiguous)
- ``ghost_success``             returns success WITHOUT applying (verification must catch it)
- ``delay_visibility``          applies, but reads do not see it for N reads (eventual consistency)
- ``apply_wrong_amount``        billing applies a different amount
- ``apply_wrong_currency``      billing applies in another currency
- ``apply_wrong_charge``        billing applies to another eligible charge
- ``apply_wrong_customer``      billing records another customer on the refund
- ``apply_wrong_status``        billing records the refund with an unexpected status
- ``apply_duplicate``           applies twice, ignoring the idempotency key
- ``apply_partial``             CRM: changes lifecycle state but drops the note
- ``apply_wrong_template``      notification: sends a different template
- ``apply_wrong_recipient``     notification: sends to the wrong address
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.simulators.store import SimCall, SimFault, SimStore

FAULT_MODES = {
    "reject", "unavailable", "apply_then_503", "drop_response_after_apply", "timeout_before_apply",
    "server_error_after_apply", "ghost_success", "delay_visibility", "apply_wrong_amount",
    "apply_wrong_currency", "apply_wrong_charge", "apply_wrong_customer", "apply_wrong_status",
    "apply_duplicate", "apply_partial", "apply_wrong_template", "apply_wrong_recipient",
}
OPERATIONS = {
    "billing": {"create_refund"},
    "subscription": {"cancel", "reactivate"},
    "identity": {"revoke", "grant"},
    "crm": {"update"},
    "notification": {"send"},
}
DEFAULT_IDEMPOTENCY_RETENTION_S = 86400.0


class SimRejection(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message


class SeedRequest(BaseModel):
    customer_id: str = Field(min_length=1, max_length=128)
    profile: str = "standard"
    idempotency_retention_s: float | None = None


class FaultRequest(BaseModel):
    system: str
    operation: str
    mode: str
    customer_id: str | None = None
    remaining: int = Field(default=1, ge=1, le=100)
    params: dict[str, Any] = Field(default_factory=dict)


class ExternalChange(BaseModel):
    """An out-of-band edit by someone other than PACT (for stale-plan / stale-restore tests)."""

    system: str
    customer_id: str
    changes: dict[str, Any]


class RefundRequest(BaseModel):
    customer_id: str
    amount: Decimal = Field(gt=0)
    currency: str = "USD"
    charge_id: str | None = None
    reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class CrmPatch(BaseModel):
    lifecycle_state: str | None = None
    note: str | None = None


class MessageRequest(BaseModel):
    customer_id: str
    to: str | None = None
    template: str
    channel: str = "email"
    variables: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReactivateRequest(BaseModel):
    plan: str | None = None


def _money(v: Any) -> Decimal:
    return Decimal(str(v)).quantize(Decimal("0.01"))


def seed_data(customer_id: str, profile: str, retention: float | None) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Deterministic seeded external state for one customer."""
    if profile == "budget":
        charges = [
            {"id": f"ch_{customer_id}_platform", "amount": "2000.00", "refunded": "0.00", "line": "platform"},
            {"id": f"ch_{customer_id}_support", "amount": "4000.00", "refunded": "0.00", "line": "support"},
            {"id": f"ch_{customer_id}_infra", "amount": "6000.00", "refunded": "0.00", "line": "infra"},
        ]
    else:
        charges = [{"id": f"ch_{customer_id}_2026_10", "amount": "499.00", "refunded": "0.00", "line": "subscription"}]
    email = f"billing+{customer_id.lower()}@customer.example"
    return {
        ("billing", "accounts", customer_id): {
            "customer_id": customer_id, "currency": "USD", "charges": charges,
            "idempotency_retention_s": retention if retention is not None else DEFAULT_IDEMPOTENCY_RETENTION_S,
        },
        ("subscription", "subscriptions", customer_id): {
            "customer_id": customer_id, "status": "active", "plan": "enterprise-premium", "version": 1,
            "monthly_price": "499.00", "period_start": "2026-10-01", "period_end": "2026-10-31",
            "unused_balance": "143.27", "history": [],
        },
        ("identity", "entitlements", customer_id): {
            "customer_id": customer_id, "entitlements": ["base", "premium"], "version": 1, "history": [],
        },
        ("crm", "accounts", customer_id): {
            "customer_id": customer_id, "lifecycle_state": "active_customer", "email": email,
            "owner": "am-team-west", "notes": [], "version": 1, "history": [],
        },
    }


def _check_version(rec: dict[str, Any], if_match: str | None) -> None:
    if if_match is not None and str(rec.get("version")) != str(if_match).strip('"'):
        raise SimRejection(412, f"precondition failed: version is {rec.get('version')}, expected {if_match}")


def create_sim_app(store: SimStore, *, manage_schema: bool = False) -> FastAPI:
    db = store.db

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if manage_schema:
            await store.create_schema()
        yield

    app = FastAPI(title="PACT simulated external systems", lifespan=lifespan)
    r = APIRouter(prefix="/sim")

    async def mutate(
        system: str, operation: str, customer_id: str, idem: str | None,
        request: dict[str, Any], apply: Callable[[AsyncSession, str | None, dict], Awaitable[tuple[int, dict]]],
        ghost: Callable[[], dict],
    ) -> JSONResponse:
        mode: str | None = None
        params: dict[str, Any] = {}
        try:
            async with db.uow() as s:
                fault = await store.take_fault(s, system, operation, customer_id)
                if fault is not None:
                    mode, params = fault.mode, dict(fault.params or {})
                if mode == "reject":
                    await store.log_call(s, system, operation, customer_id, idem, "REJECTED_BY_FAULT", request)
                    return JSONResponse({"error": params.get("message", f"{system}.{operation} rejected")}, status_code=422)
                if mode == "unavailable":
                    await store.log_call(s, system, operation, customer_id, idem, "UNAVAILABLE_BY_FAULT", request)
                    return JSONResponse({"error": "service unavailable, nothing applied"}, status_code=503)
                if mode == "ghost_success":
                    await store.log_call(s, system, operation, customer_id, idem, "GHOST_SUCCESS_BY_FAULT", request)
                    return JSONResponse(ghost(), status_code=200)
                if mode == "timeout_before_apply":
                    await store.log_call(s, system, operation, customer_id, idem, "TIMEOUT_NOT_APPLIED_BY_FAULT", request)
                    status, body = 503, {"error": "request was never applied"}
                else:
                    status, body = await apply(s, mode, params)
                    await store.log_call(s, system, operation, customer_id, idem, f"APPLIED:{mode or 'normal'}", request)
        except SimRejection as rej:
            async with db.uow() as s:
                await store.log_call(s, system, operation, customer_id, idem, f"REJECTED:{rej.status}", request)
            return JSONResponse({"error": rej.message}, status_code=rej.status)
        if mode in ("drop_response_after_apply", "timeout_before_apply"):
            # Whatever was (or was not) committed, the response never makes it back in time.
            await asyncio.sleep(float(params.get("hang_s", 6)))
        if mode == "server_error_after_apply":
            return JSONResponse({"error": "internal error"}, status_code=500)
        if mode == "apply_then_503":
            return JSONResponse({"error": "service unavailable"}, status_code=503)
        return JSONResponse(body, status_code=status)

    async def _require(s: AsyncSession, system: str, coll: str, key: str) -> dict[str, Any]:
        rec = await store.get(s, system, coll, key, lock=True)
        if rec is None:
            raise SimRejection(404, f"{system}: {key} not found")
        return dict(rec.data)

    def _public(d: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in d.items() if not k.startswith("_")}

    # ------------------------------------------------------------------ admin
    @r.post("/seed")
    async def seed(req: SeedRequest):
        async with db.uow() as s:
            await store.reset_customer(s, req.customer_id)
            for (system, coll, key), data in seed_data(req.customer_id, req.profile, req.idempotency_retention_s).items():
                await store.put(s, system, coll, key, req.customer_id, data)
        return {"seeded": req.customer_id, "profile": req.profile}

    @r.post("/faults")
    async def add_fault(req: FaultRequest):
        if req.mode not in FAULT_MODES or req.operation not in OPERATIONS.get(req.system, set()):
            raise HTTPException(422, f"unsupported fault {req.system}.{req.operation}:{req.mode}")
        async with db.uow() as s:
            s.add(SimFault(**req.model_dump()))
        return {"armed": req.model_dump()}

    @r.get("/faults")
    async def list_faults(customer_id: str | None = None):
        async with db.read() as s:
            stmt = select(SimFault).order_by(SimFault.id)
            if customer_id:
                stmt = stmt.where(SimFault.customer_id == customer_id)
            rows = (await s.execute(stmt)).scalars()
            return [{"id": f.id, "system": f.system, "operation": f.operation, "mode": f.mode,
                     "customer_id": f.customer_id, "remaining": f.remaining, "params": f.params} for f in rows]

    @r.delete("/faults")
    async def clear_faults(customer_id: str | None = None):
        async with db.uow() as s:
            stmt = delete(SimFault)
            if customer_id:
                stmt = stmt.where(SimFault.customer_id == customer_id)
            await s.execute(stmt)
        return {"cleared": True}

    @r.post("/external-change")
    async def external_change(req: ExternalChange):
        """Someone other than PACT edits provider state (bumps the record version)."""
        coll = {"subscription": "subscriptions", "identity": "entitlements", "crm": "accounts"}.get(req.system)
        if coll is None:
            raise HTTPException(422, "external changes supported for subscription, identity, crm")
        async with db.uow() as s:
            rec = await store.get(s, req.system, coll, req.customer_id, lock=True)
            if rec is None:
                raise HTTPException(404, "not found")
            data = dict(rec.data)
            data.update(req.changes)
            data["version"] = int(data.get("version", 1)) + 1
            data["history"] = [*data.get("history", []), {"op": "external_change", "changes": req.changes,
                                                           "key": None, "changed": True}]
            await store.put(s, req.system, coll, req.customer_id, req.customer_id, data)
            await store.log_call(s, req.system, "external_change", req.customer_id, None, "APPLIED:external",
                                 req.changes)
        return _public(data)

    @r.get("/calls")
    async def calls(customer_id: str, system: str | None = None, operation: str | None = None):
        async with db.read() as s:
            stmt = select(SimCall).where(SimCall.customer_id == customer_id).order_by(SimCall.id)
            if system:
                stmt = stmt.where(SimCall.system == system)
            if operation:
                stmt = stmt.where(SimCall.operation == operation)
            return [{"id": c.id, "system": c.system, "operation": c.operation, "outcome": c.outcome,
                     "idempotency_key": c.idempotency_key, "at": c.created_at.isoformat()}
                    for c in (await s.execute(stmt)).scalars()]

    @r.get("/state/{customer_id}")
    async def state(customer_id: str):
        """Ground-truth snapshot across all five systems (for the console only)."""
        async with db.read() as s:
            def one(system: str, coll: str):
                return store.get(s, system, coll, customer_id)
            billing = await one("billing", "accounts")
            sub = await one("subscription", "subscriptions")
            ident = await one("identity", "entitlements")
            crm = await one("crm", "accounts")
            refunds = await store.list(s, "billing", "refunds", customer_id)
            messages = await store.list(s, "notification", "messages", customer_id)
            return {
                "customer_id": customer_id,
                "billing": {"account": billing.data if billing else None,
                            "refunds": [_public(rf.data) for rf in refunds]},
                "subscription": sub.data if sub else None,
                "identity": ident.data if ident else None,
                "crm": crm.data if crm else None,
                "notification": {"messages": [_public(m.data) for m in messages]},
            }

    # ---------------------------------------------------------------- billing
    @r.get("/billing/customers/{customer_id}")
    async def billing_account(customer_id: str):
        async with db.read() as s:
            rec = await store.get(s, "billing", "accounts", customer_id)
            if rec is None:
                raise HTTPException(404, "billing account not found")
            return rec.data

    @r.post("/billing/refunds")
    async def create_refund(req: RefundRequest, idempotency_key: str | None = Header(default=None)):
        if not idempotency_key:
            raise HTTPException(400, "Idempotency-Key header required")

        async def apply(s: AsyncSession, mode: str | None, params: dict) -> tuple[int, dict]:
            account = await _require(s, "billing", "accounts", req.customer_id)
            retention = float(account.get("idempotency_retention_s", DEFAULT_IDEMPOTENCY_RETENTION_S))
            existing = await store.get(s, "billing", "refund_idem", idempotency_key, lock=True)
            if existing is not None and mode != "apply_duplicate" and \
                    time.time() - float(existing.data.get("at", 0)) <= retention:
                rf = await store.get(s, "billing", "refunds", existing.data["refund_id"])
                return 200, {**_public(rf.data), "idempotent_replay": True}
            if req.currency != account["currency"]:
                raise SimRejection(422, f"currency {req.currency} does not match account currency")
            amount = _money(req.amount)
            currency = req.currency
            if mode == "apply_wrong_amount":
                amount = _money(params.get("applied_amount", amount / 2))
            if mode == "apply_wrong_currency":
                currency = params.get("applied_currency", "EUR")
            target_charge = req.charge_id
            if mode == "apply_wrong_charge":
                target_charge = next((ch["id"] for ch in account["charges"]
                                      if ch["id"] != req.charge_id and
                                      _money(ch["amount"]) - _money(ch["refunded"]) >= amount), None)
                if target_charge is None:
                    raise SimRejection(422, "no other eligible charge for wrong-charge fault")
            charge = None
            for ch in account["charges"]:
                if (target_charge is None or ch["id"] == target_charge) and _money(ch["amount"]) - _money(ch["refunded"]) >= amount:
                    charge = ch
                    break
            if charge is None:
                raise SimRejection(422, "refund exceeds refundable balance for charge")
            copies = 2 if mode == "apply_duplicate" else 1
            created = []
            for _ in range(copies):
                charge["refunded"] = str(_money(charge["refunded"]) + amount)
                refund_id = f"rf_{uuid.uuid4().hex[:14]}"
                refund = {
                    "id": refund_id,
                    "customer_id": params.get("applied_customer_id", "C-OTHER")
                                   if mode == "apply_wrong_customer" else req.customer_id,
                    "amount": str(amount), "currency": currency, "charge_id": charge["id"],
                    "status": "pending" if mode == "apply_wrong_status" else "succeeded",
                    "idempotency_key": idempotency_key, "reason": req.reason, "metadata": req.metadata,
                }
                if mode == "delay_visibility":
                    refund["_hidden_reads"] = int(params.get("reads", 3))
                await store.put(s, "billing", "refunds", refund_id, req.customer_id, refund)
                await store.put(s, "billing", "refund_idem", idempotency_key, req.customer_id,
                                {"refund_id": refund_id, "at": time.time()})
                created.append(refund)
            await store.put(s, "billing", "accounts", req.customer_id, req.customer_id, account)
            return 201, _public(created[0])

        return await mutate(
            "billing", "create_refund", req.customer_id, idempotency_key, req.model_dump(mode="json"), apply,
            ghost=lambda: {"id": f"rf_{uuid.uuid4().hex[:14]}", "status": "succeeded", "amount": str(req.amount)},
        )

    @r.get("/billing/refunds")
    async def list_refunds(customer_id: str, idempotency_key: str | None = Query(default=None),
                           pact_operation: str | None = Query(default=None)):
        async with db.uow() as s:
            out = []
            for rec in await store.list(s, "billing", "refunds", customer_id):
                data = dict(rec.data)
                if idempotency_key and data.get("idempotency_key") != idempotency_key:
                    continue
                if pact_operation and (data.get("metadata") or {}).get("pact_operation") != pact_operation:
                    continue
                hidden = int(data.get("_hidden_reads", 0))
                if hidden > 0:  # eventual consistency: not yet visible to readers
                    data["_hidden_reads"] = hidden - 1
                    rec.data = data
                    continue
                out.append(_public(data))
            return {"data": out}

    # ----------------------------------------------------------- subscription
    @r.get("/subscription/customers/{customer_id}")
    async def get_subscription(customer_id: str):
        async with db.read() as s:
            rec = await store.get(s, "subscription", "subscriptions", customer_id)
            if rec is None:
                raise HTTPException(404, "subscription not found")
            return rec.data

    async def _set_subscription(customer_id: str, operation: str, target: str, idem: str | None,
                                if_match: str | None, plan: str | None = None) -> JSONResponse:
        async def apply(s: AsyncSession, mode: str | None, params: dict) -> tuple[int, dict]:
            sub = await _require(s, "subscription", "subscriptions", customer_id)
            if any(h.get("key") == idem and h.get("op") == operation for h in sub.get("history", [])):
                return 200, {**_public({k: v for k, v in sub.items() if k != "history"}), "idempotent_replay": True}
            _check_version(sub, if_match)
            changed = sub["status"] != target or (plan is not None and sub.get("plan") != plan)
            if changed:
                sub["status"] = target
                if plan:
                    sub["plan"] = plan
                sub["version"] = int(sub.get("version", 1)) + 1
            sub["history"] = [*sub.get("history", []), {"op": operation, "to": target, "key": idem,
                                                         "changed": changed, "version": sub["version"]}]
            await store.put(s, "subscription", "subscriptions", customer_id, customer_id, sub)
            return 200, {k: v for k, v in sub.items() if k != "history"}

        return await mutate("subscription", operation, customer_id, idem, {"target": target}, apply,
                            ghost=lambda: {"customer_id": customer_id, "status": target})

    @r.post("/subscription/customers/{customer_id}/cancel")
    async def cancel_subscription(customer_id: str, idempotency_key: str | None = Header(default=None),
                                  if_match: str | None = Header(default=None)):
        return await _set_subscription(customer_id, "cancel", "cancelled", idempotency_key, if_match)

    @r.post("/subscription/customers/{customer_id}/reactivate")
    async def reactivate_subscription(customer_id: str, body: ReactivateRequest | None = None,
                                      idempotency_key: str | None = Header(default=None),
                                      if_match: str | None = Header(default=None)):
        return await _set_subscription(customer_id, "reactivate", "active", idempotency_key, if_match,
                                       plan=body.plan if body else None)

    # --------------------------------------------------------------- identity
    @r.get("/identity/customers/{customer_id}/entitlements")
    async def get_entitlements(customer_id: str):
        async with db.read() as s:
            rec = await store.get(s, "identity", "entitlements", customer_id)
            if rec is None:
                raise HTTPException(404, "identity subject not found")
            return rec.data

    async def _set_entitlement(customer_id: str, entitlement: str, operation: str, idem: str | None,
                               if_match: str | None) -> JSONResponse:
        async def apply(s: AsyncSession, mode: str | None, params: dict) -> tuple[int, dict]:
            rec = await _require(s, "identity", "entitlements", customer_id)
            prior = [h for h in rec.get("history", []) if h.get("key") == idem and h.get("op") == operation]
            if prior:
                return 200, {"customer_id": customer_id, "entitlements": rec["entitlements"],
                             "version": rec["version"], "changed": prior[0]["changed"], "idempotent_replay": True}
            _check_version(rec, if_match)
            ents = set(rec["entitlements"])
            before = entitlement in ents
            if operation == "revoke":
                ents.discard(entitlement)
            else:
                ents.add(entitlement)
            changed = before != (entitlement in ents)
            rec["entitlements"] = sorted(ents)
            if changed:
                rec["version"] = int(rec.get("version", 1)) + 1
            rec["history"] = [*rec.get("history", []), {"op": operation, "entitlement": entitlement, "key": idem,
                                                         "changed": changed, "version": rec["version"]}]
            await store.put(s, "identity", "entitlements", customer_id, customer_id, rec)
            return 200, {"customer_id": customer_id, "entitlements": rec["entitlements"], "version": rec["version"],
                         "changed": changed}

        return await mutate("identity", operation, customer_id, idem, {"entitlement": entitlement}, apply,
                            ghost=lambda: {"customer_id": customer_id, "status": "ok"})

    @r.post("/identity/customers/{customer_id}/entitlements/{entitlement}/revoke")
    async def revoke(customer_id: str, entitlement: str, idempotency_key: str | None = Header(default=None),
                     if_match: str | None = Header(default=None)):
        return await _set_entitlement(customer_id, entitlement, "revoke", idempotency_key, if_match)

    @r.post("/identity/customers/{customer_id}/entitlements/{entitlement}/grant")
    async def grant(customer_id: str, entitlement: str, idempotency_key: str | None = Header(default=None),
                    if_match: str | None = Header(default=None)):
        return await _set_entitlement(customer_id, entitlement, "grant", idempotency_key, if_match)

    # -------------------------------------------------------------------- crm
    @r.get("/crm/accounts/{customer_id}")
    async def get_crm(customer_id: str):
        async with db.read() as s:
            rec = await store.get(s, "crm", "accounts", customer_id)
            if rec is None:
                raise HTTPException(404, "crm account not found")
            return rec.data

    @r.patch("/crm/accounts/{customer_id}")
    async def patch_crm(customer_id: str, patch: CrmPatch, idempotency_key: str | None = Header(default=None),
                        if_match: str | None = Header(default=None)):
        async def apply(s: AsyncSession, mode: str | None, params: dict) -> tuple[int, dict]:
            acct = await _require(s, "crm", "accounts", customer_id)
            if idempotency_key and mode != "apply_duplicate" and any(
                    h.get("key") == idempotency_key for h in acct.get("history", [])):
                return 200, {**{k: v for k, v in acct.items() if k != "history"}, "idempotent_replay": True}
            _check_version(acct, if_match)
            before = acct["lifecycle_state"]
            copies = 2 if mode == "apply_duplicate" else 1
            for _ in range(copies):
                if patch.lifecycle_state is not None:
                    acct["lifecycle_state"] = patch.lifecycle_state
                if patch.note and mode != "apply_partial":
                    acct["notes"] = [*acct.get("notes", []), patch.note]
                acct["version"] = int(acct.get("version", 1)) + 1
                acct["history"] = [*acct.get("history", []), {"from": before, "to": acct["lifecycle_state"],
                                                               "key": idempotency_key, "version": acct["version"]}]
            await store.put(s, "crm", "accounts", customer_id, customer_id, acct)
            return 200, {k: v for k, v in acct.items() if k != "history"}

        return await mutate("crm", "update", customer_id, idempotency_key, patch.model_dump(), apply,
                            ghost=lambda: {"customer_id": customer_id, "status": "ok"})

    # ----------------------------------------------------------- notification
    @r.post("/notification/messages")
    async def send_message(req: MessageRequest, idempotency_key: str | None = Header(default=None)):
        if not idempotency_key:
            raise HTTPException(400, "Idempotency-Key header required")

        async def apply(s: AsyncSession, mode: str | None, params: dict) -> tuple[int, dict]:
            existing = await store.get(s, "notification", "messages", idempotency_key, lock=True)
            if existing is not None and mode != "apply_duplicate":
                return 200, {**_public(existing.data), "idempotent_replay": True}
            copies = 2 if mode == "apply_duplicate" else 1
            msg = {}
            for i in range(copies):
                msg = {
                    "id": f"msg_{uuid.uuid4().hex[:12]}", "customer_id": req.customer_id,
                    "to": params.get("to", "wrong@elsewhere.example") if mode == "apply_wrong_recipient" else req.to,
                    "template": params.get("template", "generic_notice") if mode == "apply_wrong_template" else req.template,
                    "channel": req.channel, "variables": req.variables, "metadata": req.metadata,
                    "status": "delivered", "idempotency_key": idempotency_key,
                }
                if mode == "delay_visibility":
                    msg["status"] = "queued"
                    msg["_queued_reads"] = int(params.get("reads", 2))
                key = idempotency_key if i == 0 else f"{idempotency_key}#dup{i}"
                await store.put(s, "notification", "messages", key, req.customer_id, msg)
            return 201, _public(msg)

        return await mutate("notification", "send", req.customer_id, idempotency_key, req.model_dump(mode="json"),
                            apply, ghost=lambda: {"id": f"msg_{uuid.uuid4().hex[:12]}", "status": "queued"})

    @r.get("/notification/messages")
    async def list_messages(customer_id: str, idempotency_key: str | None = None, pact_operation: str | None = None):
        async with db.uow() as s:
            out = []
            for m in await store.list(s, "notification", "messages", customer_id):
                data = dict(m.data)
                if idempotency_key and data.get("idempotency_key") != idempotency_key:
                    continue
                if pact_operation and (data.get("metadata") or {}).get("pact_operation") != pact_operation:
                    continue
                queued = int(data.get("_queued_reads", 0))
                if queued > 0:  # queued -> delivered after a few reads
                    data["_queued_reads"] = queued - 1
                    if queued - 1 == 0:
                        data["status"] = "delivered"
                    m.data = data
                out.append(_public(data))
            return {"data": out}

    app.include_router(r)
    return app


def _standalone() -> FastAPI:
    """Entry point for running the simulators as a separate service."""
    from app.persistence.db import Database

    url = os.environ.get("PACT_SIM_DATABASE_URL") or os.environ.get(
        "PACT_DATABASE_URL", "postgresql+asyncpg://pact:pact@localhost:5432/pact"
    )
    return create_sim_app(SimStore(Database(url, pool_size=5)), manage_schema=True)


app = _standalone() if os.environ.get("PACT_SIM_STANDALONE") == "1" else None
