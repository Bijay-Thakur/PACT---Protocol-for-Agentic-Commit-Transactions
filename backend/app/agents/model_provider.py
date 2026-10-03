"""Model provider boundary (NVIDIA Nemotron or any other model plugs in here).

Rule: model proposes -> PACT validates -> PACT decides.

A planner's output is only a :class:`TransactionSpec` *proposal*. It is returned
to the caller, never executed directly; submitting it goes through
``POST /api/v1/transactions`` where the issuer policy, delegation subset checks,
payload schemas, invariants and the global commit barrier all apply. No code
path lets model output authorize a commit.
"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

import httpx

from app.config import Settings
from app.demo.specs import full_cancellation_spec
from app.domain.decision import CommitDecision
from app.domain.errors import ValidationFailed
from app.domain.transaction import TransactionSpec


class PlannerProvider(Protocol):
    name: str

    async def propose_transaction(self, intent: str, context: dict[str, Any]) -> TransactionSpec: ...


class ExplanationProvider(Protocol):
    async def explain_decision(self, decision: CommitDecision) -> str: ...


class DeterministicPlanner:
    """Template planner for the demo domain - no model, fully reproducible."""

    name = "deterministic"
    _CUSTOMER = re.compile(r"\b(C-[A-Za-z0-9\-]+)\b")
    _AMOUNT = re.compile(r"\$\s?([0-9]+(?:\.[0-9]{1,2})?)")

    async def propose_transaction(self, intent: str, context: dict[str, Any]) -> TransactionSpec:
        m = self._CUSTOMER.search(intent)
        if not m or "cancel" not in intent.lower():
            raise ValidationFailed("deterministic planner only understands customer cancellation intents "
                                   "(e.g. 'Cancel customer C-48291 and refund the unused period')",
                                   code="PLANNER_CANNOT_INTERPRET")
        amounts = self._AMOUNT.findall(intent)
        refund = context.get("refund_amount") or (amounts[0] if amounts else "143.27")
        spec = full_cancellation_spec(m.group(1).rstrip(".,"), refund_amount=str(refund))
        return TransactionSpec.model_validate(spec)


class OpenAICompatiblePlanner:
    """Planner backed by any OpenAI-compatible chat endpoint (e.g. NVIDIA NIM serving Nemotron).

    Enabled with PACT_PLANNER_PROVIDER=openai_compatible plus base URL / model / key.
    The response is parsed and schema-validated into a TransactionSpec; it still
    has to pass every deterministic PACT check when submitted.
    """

    name = "openai_compatible"

    def __init__(self, base_url: str, model: str, api_key: str):
        self.base_url, self.model, self.api_key = base_url.rstrip("/"), model, api_key

    async def propose_transaction(self, intent: str, context: dict[str, Any]) -> TransactionSpec:
        example = full_cancellation_spec("C-00000")
        prompt = (
            "Convert the business objective into a PACT TransactionSpec JSON object. "
            "Respond with JSON only, following the structure of this example exactly:\n"
            f"{json.dumps(example)}\n\nObjective: {intent}\nContext: {json.dumps(context)}"
        )
        async with httpx.AsyncClient(timeout=60) as http:
            resp = await http.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "temperature": 0, "messages": [{"role": "user", "content": prompt}]},
            )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
        start, end = text.find("{"), text.rfind("}")
        try:
            return TransactionSpec.model_validate_json(text[start:end + 1])
        except Exception as exc:  # schema validation is the first deterministic gate
            raise ValidationFailed("model output is not a valid TransactionSpec", code="PLANNER_OUTPUT_INVALID",
                                   details=str(exc)) from None


class DeterministicExplainer:
    """Renders a CommitDecision as operator-readable text. Advisory only."""

    async def explain_decision(self, decision: CommitDecision) -> str:
        if decision.eligible:
            return (f"Eligible: all {len(decision.checks)} barrier checks passed against snapshot "
                    f"version {decision.snapshot_version}.")
        failed = [c for c in decision.checks if not c.passed]
        lines = [f"Blocked by {len(decision.blocking_reasons)} reason(s):"]
        for c in failed:
            lines.append(f"- {c.blocking_reason}: {c.detail}")
        return "\n".join(lines)


def build_planner(settings: Settings) -> PlannerProvider:
    if settings.planner_provider == "openai_compatible" and settings.planner_base_url:
        return OpenAICompatiblePlanner(settings.planner_base_url, settings.planner_model, settings.planner_api_key)
    return DeterministicPlanner()
