"""Authenticated principals, credentials and sessions (single-tenant deployment, tenant-scoped keys).

- API keys: ``pact_<lookup>_<secret>``; only ``sha256(secret)`` is stored. Keys are
  256-bit random, so a fast hash is an adequate verifier. Revocable, optional expiry.
- Operator passwords: PBKDF2-HMAC-SHA256 (210k iterations, per-credential salt).
- Sessions: random cookie value; only its sha256 is stored, with a CSRF token hash.
- A request body can never assert identity: ``actor_id``/``issuer``/``tenant_id``/
  ``operator_id`` fields are compared to the authenticated principal and rejected
  when they differ.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update

from app.domain.enums import PrincipalKind
from app.domain.errors import PactError
from app.persistence.db import Database
from app.persistence.models import CredentialRow, PrincipalRow, SessionRow, TenantRow

PBKDF2_ITERATIONS = 210_000
SESSION_TTL = timedelta(hours=8)


class AuthenticationFailed(PactError):
    status_code = 401
    code = "UNAUTHENTICATED"


class Forbidden(PactError):
    status_code = 403
    code = "FORBIDDEN"


@dataclass(frozen=True)
class Principal:
    id: uuid.UUID
    tenant_id: str
    name: str
    kind: PrincipalKind
    scopes: frozenset[str]
    grants: dict[str, Any] = field(default_factory=dict)
    via: str = "api_key"  # api_key | session
    authorization_epoch: int = 1

    def has(self, scope: str) -> bool:
        return scope in self.scopes or "admin" in self.scopes

    def require(self, scope: str) -> None:
        if not self.has(scope):
            raise Forbidden(f"principal {self.name!r} lacks scope {scope!r}", code="MISSING_SCOPE",
                            details={"required": scope})

    @property
    def roles(self) -> list[str]:
        return list(self.grants.get("roles", []))

    def workflow_grant(self, workflow: str) -> dict[str, Any] | None:
        return (self.grants.get("workflows") or {}).get(workflow)


def _sha(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt_hex, dk_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations))
        return hmac.compare_digest(dk.hex(), dk_hex)
    except (ValueError, TypeError):
        return False


def _to_principal(row: PrincipalRow, via: str) -> Principal:
    return Principal(id=row.id, tenant_id=row.tenant_id, name=row.name, kind=PrincipalKind(row.kind),
                     scopes=frozenset(row.scopes or []), grants=dict(row.grants or {}), via=via,
                     authorization_epoch=row.authorization_epoch)


class PrincipalService:
    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------ management
    async def ensure_tenant(self, tenant_id: str, name: str | None = None) -> None:
        async with self.db.uow() as s:
            if await s.get(TenantRow, tenant_id) is None:
                s.add(TenantRow(id=tenant_id, name=name or tenant_id))

    async def upsert_principal(self, tenant_id: str, name: str, kind: PrincipalKind, scopes: list[str],
                               grants: dict[str, Any]) -> Principal:
        await self.ensure_tenant(tenant_id)
        async with self.db.uow() as s:
            row = (await s.execute(select(PrincipalRow).where(
                PrincipalRow.tenant_id == tenant_id, PrincipalRow.name == name))).scalar_one_or_none()
            if row is None:
                row = PrincipalRow(tenant_id=tenant_id, name=name, kind=str(kind), scopes=sorted(scopes),
                                   grants=grants, status="ACTIVE")
                s.add(row)
            else:
                if row.kind != str(kind) or row.scopes != sorted(scopes) or row.grants != grants:
                    row.authorization_epoch += 1
                row.kind, row.scopes, row.grants = str(kind), sorted(scopes), grants
            await s.flush()
            return _to_principal(row, "admin")

    async def issue_api_key(self, principal_id: uuid.UUID, label: str = "",
                            ttl: timedelta | None = None) -> str:
        lookup = secrets.token_hex(8)
        secret = secrets.token_urlsafe(32)
        async with self.db.uow() as s:
            s.add(CredentialRow(principal_id=principal_id, kind="API_KEY", lookup=lookup, verifier=_sha(secret),
                                label=label, expires_at=_now() + ttl if ttl else None))
        return f"pact_{lookup}_{secret}"

    async def set_password(self, principal_id: uuid.UUID, username: str, password: str) -> None:
        async with self.db.uow() as s:
            await s.execute(update(CredentialRow).where(
                CredentialRow.principal_id == principal_id, CredentialRow.kind == "PASSWORD",
                CredentialRow.revoked_at.is_(None)).values(revoked_at=_now()))
            s.add(CredentialRow(principal_id=principal_id, kind="PASSWORD", lookup=_sha(username.lower())[:64],
                                verifier=hash_password(password), label=f"login:{username}"))

    async def revoke_principal(self, principal_id: uuid.UUID) -> None:
        async with self.db.uow() as s:
            row = await s.get(PrincipalRow, principal_id)
            row.status, row.revoked_at = "REVOKED", _now()
            row.authorization_epoch += 1
            await s.execute(update(CredentialRow).where(CredentialRow.principal_id == principal_id,
                                                        CredentialRow.revoked_at.is_(None)).values(revoked_at=_now()))
            await s.execute(update(SessionRow).where(SessionRow.principal_id == principal_id,
                                                     SessionRow.revoked_at.is_(None)).values(revoked_at=_now()))

    async def by_name(self, tenant_id: str, name: str) -> Principal | None:
        async with self.db.read() as s:
            row = (await s.execute(select(PrincipalRow).where(
                PrincipalRow.tenant_id == tenant_id, PrincipalRow.name == name))).scalar_one_or_none()
            return _to_principal(row, "lookup") if row is not None and row.status == "ACTIVE" else None

    async def by_id(self, principal_id: uuid.UUID) -> Principal | None:
        async with self.db.read() as s:
            row = await s.get(PrincipalRow, principal_id)
            return _to_principal(row, "lookup") if row is not None and row.status == "ACTIVE" else None

    # -------------------------------------------------------- authentication
    async def authenticate_api_key(self, token: str) -> Principal:
        parts = token.split("_", 2)
        if len(parts) != 3 or parts[0] != "pact":
            raise AuthenticationFailed("malformed API key")
        _, lookup, secret = parts
        async with self.db.uow() as s:
            creds = (await s.execute(select(CredentialRow).where(
                CredentialRow.lookup == lookup, CredentialRow.kind == "API_KEY"))).scalars().all()
            for cred in creds:
                if hmac.compare_digest(cred.verifier, _sha(secret)):
                    if cred.revoked_at is not None or (cred.expires_at and cred.expires_at <= _now()):
                        raise AuthenticationFailed("credential revoked or expired")
                    row = await s.get(PrincipalRow, cred.principal_id)
                    if row is None or row.status != "ACTIVE":
                        raise AuthenticationFailed("principal revoked")
                    cred.last_used_at = _now()
                    return _to_principal(row, "api_key")
        raise AuthenticationFailed("invalid API key")

    async def login(self, tenant_id: str, username: str, password: str) -> tuple[Principal, str, str]:
        """Returns (principal, session_cookie_value, csrf_token)."""
        async with self.db.uow() as s:
            creds = (await s.execute(select(CredentialRow).where(
                CredentialRow.kind == "PASSWORD", CredentialRow.lookup == _sha(username.lower())[:64],
                CredentialRow.revoked_at.is_(None)))).scalars().all()
            for cred in creds:
                row = await s.get(PrincipalRow, cred.principal_id)
                if row is not None and row.tenant_id == tenant_id and row.status == "ACTIVE" \
                        and verify_password(password, cred.verifier):
                    if PrincipalKind(row.kind) != PrincipalKind.OPERATOR:
                        raise AuthenticationFailed("only operators may log in to the console")
                    session_value, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
                    s.add(SessionRow(id=_sha(session_value), principal_id=row.id, csrf_hash=_sha(csrf),
                                     expires_at=_now() + SESSION_TTL))
                    cred.last_used_at = _now()
                    return _to_principal(row, "session"), session_value, csrf
        raise AuthenticationFailed("invalid username or password")

    async def authenticate_session(self, session_value: str, csrf_header: str | None, *, unsafe: bool) -> Principal:
        async with self.db.read() as s:
            sess = await s.get(SessionRow, _sha(session_value))
            if sess is None or sess.revoked_at is not None or sess.expires_at <= _now():
                raise AuthenticationFailed("session expired")
            if unsafe and (not csrf_header or not hmac.compare_digest(sess.csrf_hash, _sha(csrf_header))):
                raise Forbidden("missing or invalid CSRF token", code="CSRF_REJECTED")
            row = await s.get(PrincipalRow, sess.principal_id)
            if row is None or row.status != "ACTIVE":
                raise AuthenticationFailed("principal revoked")
            return _to_principal(row, "session")

    async def logout(self, session_value: str) -> None:
        async with self.db.uow() as s:
            sess = await s.get(SessionRow, _sha(session_value))
            if sess is not None:
                sess.revoked_at = _now()


def reject_forged(principal: Principal, **claimed: str | None) -> None:
    """A body field that names an identity must equal the authenticated one (A02)."""
    expected = {"actor_id": principal.name, "operator_id": principal.name, "tenant_id": principal.tenant_id,
                "issuer": None}
    for field_name, value in claimed.items():
        if value is None:
            continue
        if field_name == "issuer" or value != expected[field_name]:
            raise Forbidden(f"{field_name}={value!r} does not match the authenticated principal; identity and "
                            "authority come from credentials and server-side grants, not request bodies",
                            code="FORGED_IDENTITY", details={"field": field_name})
