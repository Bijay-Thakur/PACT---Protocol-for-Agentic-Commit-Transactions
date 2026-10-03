"""Domain errors with machine-readable codes.

Every failure the coordinator can produce maps to a stable ``code`` so callers
never have to parse generic 500s.
"""

from __future__ import annotations

from typing import Any


class PactError(Exception):
    status_code = 400
    code = "PACT_ERROR"

    def __init__(self, message: str, *, code: str | None = None, details: Any = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


class NotFound(PactError):
    status_code = 404
    code = "NOT_FOUND"


class ValidationFailed(PactError):
    status_code = 422
    code = "VALIDATION_FAILED"


class AuthorityViolation(PactError):
    status_code = 403
    code = "AUTHORITY_VIOLATION"


class InvalidStateTransition(PactError):
    status_code = 409
    code = "INVALID_STATE_TRANSITION"


class StateConflict(PactError):
    """The aggregate is not in a state that permits the requested operation."""

    status_code = 409
    code = "STATE_CONFLICT"


class ConcurrencyConflict(PactError):
    status_code = 409
    code = "CONCURRENCY_CONFLICT"


class ReceiptNotFinal(PactError):
    status_code = 409
    code = "RECEIPT_NOT_FINAL"
