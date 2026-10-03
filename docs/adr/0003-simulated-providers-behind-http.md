# ADR-0003: Simulated providers as independent HTTP services

**Status:** accepted

**Decision.** The five external systems are a separate FastAPI app (`app/simulators`) with their own SQLAlchemy metadata, their own state and optionally their own database. PACT adapters reach them only via HTTPX. That means an in-process ASGI transport in development and tests, and a separate `simulators` container (database `pact_sim`) in docker compose. Faults are injected provider-side, and a provider-side call log records every request.

**Why.** Verification must read state the executor cannot fake. With a real HTTP boundary, "the refund call timed out" and "the refund exists" are genuinely different observations. "Exactly one `create_refund` call" is provable from the provider's own log.

**Consequences.** Real SaaS adapters can replace the mock adapters without touching core code, because core never contains provider-specific logic. The separate-service topology was exercised natively (standalone simulator process on its own database, all nine scenarios passing).
