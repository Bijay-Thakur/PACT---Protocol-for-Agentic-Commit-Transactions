"""Model text is a bounded proposal, never an authority source."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from app.agents.model_provider import DeterministicPlanner, OpenAICompatiblePlanner, PlanProposal, build_planner
from app.config import Settings
from app.api.planner import ReviewRequest, review
from app.domain.errors import ValidationFailed
from app.domain.enums import PrincipalKind
from app.policy.workflows import default_workflows
from app.security.principals import Principal


def test_privileged_model_fields_are_rejected():
    base = {"objective": "Cancel customer C-123", "requested_workflow": "customer_offboarding"}
    for added in ({"issuer": "root"}, {"approval": True}, {"policy": "skip checks"},
                  {"candidate_actions": ["x" * 501]}):
        with pytest.raises(ValidationError):
            PlanProposal.model_validate({**base, **added})


async def test_live_provider_sanitizes_usage_and_rejects_malformed_output(monkeypatch):
    planner = OpenAICompatiblePlanner("https://model.example/v1", "nemotron-test", "private")
    proposal = {"objective": "Cancel customer C-123", "requested_workflow": "customer_offboarding",
                "entity_references": [{"key": "customer_id", "value": "C-123"}],
                "candidate_actions": [], "requested_parameters": [], "unresolved_questions": []}
    raw = {"choices": [{"message": {"content": json.dumps(proposal)}}],
           "usage": {"prompt_tokens": 10, "total_tokens": 20, "secret": "do-not-store"}}
    class Client:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return None
        async def post(self, url, **kwargs):
            assert kwargs["headers"]["Authorization"] == "Bearer private"
            assert kwargs["json"]["max_tokens"] == 600
            return httpx.Response(200, json=raw, headers={"x-request-id": "req-123"},
                                  request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    result = await planner.propose_transaction("Cancel C-123", {})
    assert result.requested_workflow == "customer_offboarding"
    assert planner.last_usage == {"prompt_tokens": 10, "total_tokens": 20}
    assert planner.last_request_id == "req-123"
    assert planner.last_latency_ms is not None
    raw["choices"][0]["message"]["content"] = json.dumps({**proposal, "operator_id": "root"})
    with pytest.raises(ValidationFailed) as err:
        await planner.propose_transaction("Cancel C-123", {})
    assert err.value.code == "PLANNER_OUTPUT_INVALID"


async def test_model_outage_never_falls_back_to_fixture(monkeypatch):
    planner = OpenAICompatiblePlanner("https://model.example/v1", "nemotron-test", "private")
    class Client:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return None
        async def post(self, url, **kwargs):
            raise httpx.ConnectError("offline")
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    with pytest.raises(ValidationFailed) as err:
        await planner.propose_transaction("Cancel C-123", {})
    assert err.value.code == "PLANNER_OUTPUT_INVALID"
    with pytest.raises(ValidationFailed) as err:
        OpenAICompatiblePlanner("", "nemotron-test", "private")
    assert err.value.code == "PLANNER_NOT_CONFIGURED"
    with pytest.raises(ValidationFailed) as err:
        OpenAICompatiblePlanner("https://model.example/v1", "unrelated-model", "private")
    assert err.value.code == "PLANNER_NOT_CONFIGURED"
    generic = OpenAICompatiblePlanner("https://model.example/v1", "unrelated-model", "private",
                                      provider_name="openai_compatible")
    assert generic.name == "openai_compatible"


async def test_groq_uses_private_environment_key_and_catalog_is_opt_in(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "private-groq-key")
    base = dict(planner_provider="groq", planner_base_url="https://api.groq.com/openai/v1",
                planner_model="openai/gpt-oss-20b", planner_api_key="")
    without_catalog = build_planner(Settings(**base, planner_share_workflow_catalog=False))
    with_catalog = build_planner(Settings(**base, planner_share_workflow_catalog=True))
    assert without_catalog.name == with_catalog.name == "groq_dev"
    assert without_catalog.api_key == with_catalog.api_key == "private-groq-key"
    assert without_catalog.workflow_catalog == []
    assert any(item["key"] == "customer_offboarding" for item in with_catalog.workflow_catalog)
    offboarding = next(item for item in with_catalog.workflow_catalog
                       if item["key"] == "customer_offboarding")
    assert "customer_id" in offboarding["required_business_fields"]
    assert "reason" in offboarding["optional_business_fields"]

    proposal = {"objective": "Cancel customer C-123", "requested_workflow": "customer_offboarding",
                "entity_references": [{"key": "customer_id", "value": "C-123"}],
                "candidate_actions": [], "requested_parameters": [], "unresolved_questions": []}
    seen = []
    class Client:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return None
        async def post(self, url, **kwargs):
            seen.append(kwargs["json"]["messages"])
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(proposal)}}]},
                                  request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    await without_catalog.propose_transaction("Cancel customer C-123", {})
    await with_catalog.propose_transaction("Cancel customer C-123", {})
    assert "customer_offboarding" not in seen[0][0]["content"]
    assert "customer_offboarding" in seen[1][0]["content"]
    assert all("private-groq-key" not in json.dumps(messages) for messages in seen)


def test_explicit_nebius_profile_cannot_borrow_legacy_groq_key(monkeypatch):
    monkeypatch.delenv("NEBIUS_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "groq-only")
    settings = Settings(model_profile="nebius_nemotron", planner_provider="nebius_nemotron",
                        planner_api_key="old-groq-key", planner_base_url="", planner_model="")
    with pytest.raises(ValidationFailed) as error:
        build_planner(settings)
    assert error.value.code == "PLANNER_NOT_CONFIGURED"
    check = build_planner(settings, configuration_only=True)
    assert check.name == "nebius_nemotron"


def test_both_keys_never_change_explicit_profile_and_http_is_rejected(monkeypatch):
    monkeypatch.setenv("NEBIUS_API_KEY", "nebius-only")
    monkeypatch.setenv("GROQ_API_KEY", "groq-only")
    base = dict(planner_provider="groq_dev", planner_api_key="", planner_base_url="", planner_model="")
    groq = build_planner(Settings(**base, model_profile="groq_dev"))
    assert groq.name == "groq_dev" and groq.api_key == "groq-only"
    nebius = build_planner(Settings(**{**base, "planner_provider": "nebius_nemotron"},
                                    model_profile="nebius_nemotron"))
    assert nebius.name == "nebius_nemotron" and nebius.api_key == "nebius-only"
    with pytest.raises(ValidationFailed) as error:
        build_planner(Settings(**base, model_profile="groq_dev",
                               groq_base_url="http://api.groq.com/openai/v1"))
    assert error.value.code == "PLANNER_NOT_CONFIGURED"


async def test_unknown_model_workflow_is_reviewed_as_clarification():
    principal = Principal(uuid4(), "test", "agent", PrincipalKind.AGENT,
                          frozenset({"planner:propose"}))
    response = await review(ReviewRequest(proposal=PlanProposal(
        objective="Cancel customer C-123", requested_workflow="CancelCustomer")),
        principal, SimpleNamespace(workflows=default_workflows()))
    assert response["status"] == "NEEDS_CLARIFICATION"
    assert response["issues"] == [{"code": "UNKNOWN_WORKFLOW", "workflow": "CancelCustomer"}]
    assert response["applied"] is False


@pytest.mark.parametrize("profile,expected_format", [
    ("groq_dev", {"type": "json_schema", "strict": True}),
    ("nebius_nemotron", {"type": "json_schema", "strict": None}),
])
async def test_profile_wire_contract_and_typed_provider_failures(monkeypatch, profile, expected_format):
    from app.agents.model_provider import WIRE_SCHEMA
    monkeypatch.setenv("GROQ_API_KEY", "test-groq")
    monkeypatch.setenv("NEBIUS_API_KEY", "test-nebius")
    planner = build_planner(Settings(model_profile=profile, planner_provider=profile,
                                     planner_api_key="", planner_base_url="", planner_model=""))
    captured = []
    status = 200
    finish = "stop"
    refusal = False
    provider_error = None

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return None
        async def post(self, url, **kwargs):
            captured.append((url, kwargs))
            wire = {"objective": "Cancel C-123", "requested_workflow": "customer_offboarding",
                    "entity_references": [{"key": "customer_id", "value": "C-123"}],
                    "candidate_actions": [], "requested_parameters": [], "unresolved_questions": []}
            if provider_error is not None:
                return httpx.Response(status, json={"error": {"message": provider_error}},
                    request=httpx.Request("POST", url))
            return httpx.Response(status, json={"choices": [{"finish_reason": finish,
                "message": {"content": json.dumps(wire), "refusal": refusal}}]},
                request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    assert (await planner.propose_transaction("Cancel C-123", {})).entity_references == {"customer_id": "C-123"}
    fmt = captured[-1][1]["json"]["response_format"]
    assert fmt["type"] == expected_format["type"]
    assert fmt["json_schema"].get("strict") == expected_format["strict"]
    schema = fmt["json_schema"].get("schema", fmt["json_schema"])
    assert schema == WIRE_SCHEMA
    assert captured[-1][0].startswith("https://")

    for bad_status, expected in [(401, "PLANNER_AUTH"), (403, "PLANNER_ACCESS_DENIED"),
                                 (429, "PLANNER_RATE_LIMIT"),
                                 (404, "PLANNER_MODEL_UNAVAILABLE")]:
        status = bad_status
        with pytest.raises(ValidationFailed) as error:
            await planner.propose_transaction("Cancel C-123", {})
        assert error.value.code == expected
    status = 200
    status = 400
    provider_error = "response_format json_schema unavailable for this model"
    with pytest.raises(ValidationFailed) as error:
        await planner.propose_transaction("Cancel C-123", {})
    assert error.value.code == "PLANNER_SCHEMA_UNSUPPORTED"
    provider_error = None
    status = 200
    finish = "length"
    with pytest.raises(ValidationFailed) as error:
        await planner.propose_transaction("Cancel C-123", {})
    assert error.value.code == "PLANNER_TRUNCATED"
    finish = "stop"
    refusal = True
    with pytest.raises(ValidationFailed) as error:
        await planner.propose_transaction("Cancel C-123", {})
    assert error.value.code == "PLANNER_REFUSAL"


async def test_versioned_intent_corpus_records_fixture_coverage():
    path = Path(__file__).parent / "fixtures" / "model_intents_v1.jsonl"
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(cases) >= 50
    assert sum(c["split"] == "heldout" for c in cases) >= 10
    planner = DeterministicPlanner()
    counts = {"PROPOSAL": 0, "CLARIFY": 0}
    for case in cases:
        try:
            proposal = await planner.propose_transaction(case["intent"], {})
            actual = "PROPOSAL"
            assert proposal.requested_workflow == "customer_offboarding"
            assert proposal.entity_references["customer_id"] == case["customer_id"]
            assert not any(k in proposal.model_fields_set for k in ("issuer", "approval", "policy"))
        except ValidationFailed:
            actual = "CLARIFY"
        assert actual == case["expected"], case
        counts[actual] += 1
    assert counts["PROPOSAL"] > 0 and counts["CLARIFY"] > 0
