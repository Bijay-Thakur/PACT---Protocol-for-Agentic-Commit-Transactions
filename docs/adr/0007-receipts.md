# ADR-0007: Canonical, immutable, chained receipts

**Status:** accepted

**Decision.** A receipt is generated for every terminal transaction, children first and then the root. The payload is normalized (sorted keys; canonical strings for decimals, datetimes and UUIDs), hashed with SHA-256 excluding `receipt_hash`, and stored once (`UNIQUE(transaction_id)`, an immutability trigger, no mutation routes). The root receipt includes each child's receipt hash. `GET /receipt/verify` recomputes the digest from the stored payload. A non-terminal transaction gets a `409` with an unhashed draft.

**Consequences.** A receipt answers what was attempted, under whose authority, what the barrier decided, what executed, what verification showed, what was reconciled or compensated, what remains uncompensated, and which operators intervened. Signing is a post-MVP addition: sign the existing digest.
