"""PACT local administration and simulator commands (run from backend/).

    python -m app.cli migrate
    python -m app.cli create-operator TENANT USERNAME
    python -m app.cli create-requester TENANT USERNAME
    python -m app.cli create-agent TENANT NAME GRANTS_JSON_FILE
    python -m app.cli scenario [name ...]
    python -m app.cli crash-midflight
    python -m app.cli recover
    python -m app.cli planner-smoke "Cancel customer C-123"
"""

from __future__ import annotations

import asyncio
import argparse
import getpass
import json
import os
import sys
import uuid
from dataclasses import replace
from pathlib import Path
from uuid import UUID

from app.config import Settings


async def _model_command(command: str, args: list[str]) -> int:
    """Bounded synthetic model check and evaluation; no business effects."""
    from app.agents.model_provider import build_planner
    from app.domain.errors import ValidationFailed
    parser = argparse.ArgumentParser(prog=f"python -m app.cli {command}")
    parser.add_argument("--profile", required=True,
                        choices=["groq_dev", "nebius_nemotron", "deterministic_fixture"])
    if command == "model-check":
        parser.add_argument("--live", action="store_true")
    else:
        parser.add_argument("--max-calls", type=int, required=True)
        parser.add_argument("--tenant", default="model-evaluation")
        parser.add_argument("--share-workflow-catalog", action="store_true")
        parser.add_argument("--delay-ms", type=int, default=0)
    if command == "model-eval":
        parser.add_argument("--dataset", required=True)
        parser.add_argument("--report")
    parsed = parser.parse_args(args)
    settings = replace(Settings(), model_profile=parsed.profile,
                       planner_share_workflow_catalog=bool(getattr(parsed, "share_workflow_catalog", False)))
    actual_key = os.environ.get(
        "GROQ_API_KEY" if parsed.profile == "groq_dev" else "NEBIUS_API_KEY", "")
    try:
        planner = build_planner(settings, configuration_only=command == "model-check")
    except ValidationFailed as exc:
        print(json.dumps({"profile": parsed.profile, "configured": False,
                          "error": exc.code, "live_verified": False}))
        return 1
    if command == "model-check":
        report = {"profile": parsed.profile, "provider": planner.name,
                  "model": getattr(planner, "model", None),
                  "endpoint": getattr(planner, "base_url", None),
                  "response_format": getattr(planner, "response_format", "fixture"),
                  "configured": True, "key_present": bool(actual_key),
                  "live_verified": False}
        if parsed.live and parsed.profile != "deterministic_fixture" and actual_key:
            import httpx
            try:
                async with httpx.AsyncClient(timeout=10) as http:
                    response = await http.get(f"{planner.base_url}/models",
                                              headers={"Authorization": f"Bearer {planner.api_key}"})
                    response.raise_for_status()
                report["live_verified"] = any(m.get("id") == planner.model
                                               for m in response.json().get("data", []))
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                report["preflight_error_type"] = type(exc).__name__
        print(json.dumps(report))
        return 0 if not parsed.live or report["live_verified"] else 1
    if parsed.max_calls < 1 or parsed.max_calls > 60:
        parser.error("--max-calls must be between 1 and 60")
    if parsed.delay_ms < 0 or parsed.delay_ms > 30000:
        parser.error("--delay-ms must be between 0 and 30000")
    if command == "model-smoke":
        cases = [{"id": "smoke", "intent": "Cancel customer C-TEST-001 and refund the unused period",
                  "customer_id": "C-TEST-001"}]
    else:
        cases = [json.loads(line) for line in Path(parsed.dataset).read_text(encoding="utf-8").splitlines()
                 if line.strip()]
        if len(cases) < 50 or sum(c.get("split") == "heldout" for c in cases) < 10:
            parser.error("evaluation corpus needs 50 cases including 10 held out")
    results = []
    observed_tokens = 0
    unknown_usage = 0
    from types import SimpleNamespace
    from app.api.planner import ProposeRequest, ReviewRequest, propose, review
    from app.agents.model_provider import PlanProposal
    from app.domain.enums import PrincipalKind
    from app.persistence.db import Database
    from app.policy.workflows import default_workflows
    from app.security.principals import Principal
    db = Database(settings.database_url)
    eval_principal = Principal(UUID(int=0), parsed.tenant, "model-evaluation-cli",
                               PrincipalKind.SERVICE, frozenset({"planner:propose"}))
    eval_rt = SimpleNamespace(planner=planner, settings=settings, db=db, workflows=default_workflows())
    try:
        for index, case in enumerate(cases[:parsed.max_calls]):
            if index and parsed.delay_ms:
                await asyncio.sleep(parsed.delay_ms / 1000)
            try:
                traced = await propose(ProposeRequest(intent=case["intent"]), eval_principal, eval_rt)
                proposal = traced["proposed_plan"]
                reviewed = await review(ReviewRequest(proposal=PlanProposal.model_validate(proposal)),
                                        eval_principal, eval_rt)
                result = {"case_id": case.get("id"), "status": "PROPOSAL",
                          "split": case.get("split"), "category": case.get("category"),
                          "proposal_trace_id": traced["proposal_trace_id"],
                          "latency_ms": getattr(planner, "last_latency_ms", None),
                          "critical_entity_agreement": proposal["entity_references"].get("customer_id") == case.get("customer_id"),
                          "review_status": reviewed["status"],
                          "review_issue_codes": [issue["code"] for issue in reviewed["issues"]],
                          "intent_issue_codes": [issue["code"] for issue in traced["intent_issues"]]}
            except ValidationFailed as exc:
                result = {"case_id": case.get("id"), "status": exc.code,
                          "split": case.get("split"), "category": case.get("category"),
                          "latency_ms": getattr(planner, "last_latency_ms", None),
                          "critical_entity_agreement": False}
            result["expected"] = case.get("expected")
            result["expected_match"] = None if case.get("expected") is None else (
                result["status"] == "PROPOSAL" and result["critical_entity_agreement"]
                if case.get("expected") == "PROPOSAL" else
                result["status"] != "PROPOSAL" or result.get("review_status") == "NEEDS_CLARIFICATION"
            )
            results.append(result)
            usage = getattr(planner, "last_usage", None)
            if usage and isinstance(usage.get("total_tokens"), int):
                observed_tokens += usage["total_tokens"]
            else:
                unknown_usage += 1
    finally:
        await db.dispose()
    report = {"profile": parsed.profile, "model": getattr(planner, "model", None),
              "live": parsed.profile != "deterministic_fixture", "calls": len(results),
              "valid": sum(r["status"] == "PROPOSAL" for r in results),
              "critical_entity_agreement": sum(r["critical_entity_agreement"] for r in results),
              "observed_tokens": observed_tokens, "unknown_usage": unknown_usage,
              "unsafe_executions": 0, "applied": False, "results": results}
    heldout = [r for r in results if r.get("split") == "heldout"]
    measured_latency = sorted(float(r["latency_ms"]) for r in results
                              if isinstance(r.get("latency_ms"), (int, float)))
    valid_subset = [r for r in results if r.get("category") == "valid"]
    report["expected_match"] = sum(bool(r["expected_match"]) for r in results)
    report["valid_reviewable_correct"] = sum(r["status"] == "PROPOSAL" and
        r["critical_entity_agreement"] and r.get("review_status") == "REVIEWABLE_REQUEST"
        for r in valid_subset)
    report["valid_subset_cases"] = len(valid_subset)
    report["false_blocks"] = sum(r["status"] != "PROPOSAL" or
        r.get("review_status") != "REVIEWABLE_REQUEST" for r in valid_subset)
    report["unexpected_reviewable"] = sum(r["expected"] == "CLARIFY" and
        r.get("review_status") == "REVIEWABLE_REQUEST" for r in results)
    report["latency_ms"] = {"measured": len(measured_latency),
                            "p50": measured_latency[len(measured_latency) // 2] if measured_latency else None,
                            "p95": measured_latency[min(len(measured_latency) - 1,
                                                        int(len(measured_latency) * .95))]
                            if measured_latency else None}
    report["required_outcome_coverage"] = "NOT_MEASURED_BY_PROPOSAL_EVAL"
    report["heldout"] = {"cases": len(heldout),
                         "expected_match": sum(bool(r["expected_match"]) for r in heldout),
                         "critical_entity_agreement": sum(bool(r["critical_entity_agreement"])
                                                          for r in heldout if r["expected"] == "PROPOSAL"),
                         "expected_proposals": sum(r["expected"] == "PROPOSAL" for r in heldout)}
    valid_rate = report["valid_reviewable_correct"] / len(valid_subset) if valid_subset else 0.0
    report["acceptance"] = {"valid_reviewable_rate": round(valid_rate, 4),
                            "target": 0.9, "passed": (
                                len(results) >= 50 and len(heldout) >= 10 and valid_rate >= 0.9
                                and report["unexpected_reviewable"] == 0
                                and report["unsafe_executions"] == 0) if command == "model-eval" else
                            all(r["status"] == "PROPOSAL" and r["critical_entity_agreement"]
                                and r.get("review_status") == "REVIEWABLE_REQUEST" for r in results)}
    if command == "model-eval" and parsed.report:
        Path(parsed.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))
    return 0 if report["acceptance"]["passed"] else 1


def _migrate() -> None:
    from alembic import command
    from alembic.config import Config

    backend = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "migrations"))
    command.upgrade(cfg, "head")


async def _provision(kind: str, args: list[str]) -> int:
    from app.domain.enums import PrincipalKind
    from app.runtime import Runtime

    rt = Runtime(Settings())
    try:
        if kind in {"create-operator", "create-requester"} and len(args) == 2:
            tenant, username = args
            password = getpass.getpass("Operator password: ")
            if password != getpass.getpass("Confirm password: ") or len(password) < 12:
                raise ValueError("passwords must match and be at least 12 characters")
            if kind == "create-requester":
                scopes = ["tx:begin", "tx:propose", "tx:prepare", "tx:commit", "tx:abort",
                          "planner:propose"]
                grants = {"workflows": {"customer_offboarding": {
                    "amount_limit": "500.00", "cumulative_amount_limit": "500.00",
                    "approval_threshold": "100.00"}}}
            else:
                scopes = ["op:approve", "op:recover", "op:attest", "tx:read_all", "demo:run",
                          "planner:propose"]
                grants = {"roles": ["refund_approver", "finance_approver", "code_approver"]}
            p = await rt.principals.upsert_principal(tenant, username, PrincipalKind.OPERATOR,
                                                     scopes, grants)
            await rt.principals.set_password(p.id, username, password)
            print(json.dumps({"principal": username, "tenant": tenant, "kind": "OPERATOR"}))
            return 0
        if kind == "create-agent" and len(args) == 3:
            tenant, name, path = args
            grants = json.loads(Path(path).read_text(encoding="utf-8"))
            p = await rt.principals.upsert_principal(tenant, name, PrincipalKind.AGENT,
                ["tx:begin", "tx:delegate", "tx:propose", "tx:prepare", "tx:commit", "tx:abort"], grants)
            key = await rt.principals.issue_api_key(p.id, label="local CLI provision")
            print(json.dumps({"principal": name, "tenant": tenant, "api_key_once": key}))
            return 0
        print(__doc__)
        return 2
    finally:
        await rt.stop()


async def _scenarios(names: list[str]) -> int:
    from app.api.demo import RunRequest, run
    from app.demo.scenarios import SCENARIOS
    from app.domain.enums import PrincipalKind
    from app.runtime import Runtime

    settings = replace(Settings(), demo_mode=True, embedded_worker=False, auto_recover_on_startup=False)
    rt = Runtime(settings)
    await rt.start()
    failures = 0
    try:
        tenant = "cli-demo"
        p = await rt.principals.upsert_principal(tenant, "cli_operator", PrincipalKind.OPERATOR,
            ["demo:run", "tx:read_all"], {})
        for name in names or list(SCENARIOS):
            result = await run(name, RunRequest(), p, rt)
            ok = result["state"] == result["expected_state"]
            failures += not ok
            print(json.dumps({"scenario": name, "state": result["state"], "expected": result["expected_state"],
                              "ok": ok, "root_id": result["root_id"],
                              "blocking_reasons": (result.get("commit_decision") or {}).get("blocking_reasons")}))
    finally:
        await rt.stop()
    return int(failures)


async def _crash_midflight() -> None:
    from app.api.demo import _make_offboarding
    from app.domain.transaction import CommitRequest
    from app.persistence.models import EffectRow
    from app.runtime import Runtime
    from app.worker import Worker

    async def hook(point: str, effect_id: UUID) -> None:
        if point != "after_external_call":
            return
        async with rt.db.read() as s:
            effect = await s.get(EffectRow, effect_id)
        if effect.contract_type == "billing.refund":
            print(json.dumps({"event": "SIMULATED_PROCESS_CRASH", "root_id": str(effect.root_id),
                              "effect_id": str(effect_id)}), flush=True)
            os._exit(137)

    settings = replace(Settings(), demo_mode=True, embedded_worker=False, auto_recover_on_startup=False)
    rt = Runtime(settings, crash_hook=hook)
    await rt.start()
    cid = os.environ.get("PACT_CRASH_CUSTOMER") or f"C-CRASH-{uuid.uuid4().hex[:6].upper()}"
    try:
        (await rt.http.post("/sim/seed", json={"customer_id": cid})).raise_for_status()
        actor, root = await _make_offboarding(rt, "cli-demo", cid, "143.27", False)
        frozen = await rt.coordinator.prepare(actor, root)
        assert frozen["status"] == "FROZEN", frozen
        await rt.coordinator.request_commit(actor, root, CommitRequest(revision_digest=frozen["digest"]))
        print(json.dumps({"event": "ROOT_CREATED", "root_id": str(root), "customer_id": cid}), flush=True)
        worker = Worker(rt)
        for _ in range(60):
            await worker.run_once(root)
        raise RuntimeError("crash hook did not run")
    finally:
        await rt.stop()


async def _recover() -> None:
    from app.runtime import Runtime
    from app.worker import Worker
    from sqlalchemy import select
    from app.persistence.models import TransactionRow

    rt = Runtime(Settings())
    await rt.start()
    try:
        await rt.coordinator.sweep(stranded_grace_s=0)
        worker = Worker(rt)
        for _ in range(400):
            progressed = await worker.run_once()
            if not progressed:
                await asyncio.sleep(0.05)
        async with rt.db.read() as s:
            rows = (await s.execute(select(TransactionRow).where(TransactionRow.parent_id.is_(None))
                                    .order_by(TransactionRow.created_at.desc()).limit(20))).scalars()
            print(json.dumps({"event": "RECOVERY_CHECKPOINT", "recent_roots": [
                {"root_id": str(t.id), "state": t.state} for t in rows]}))
    finally:
        await rt.stop()


async def _planner_smoke(intent: str) -> int:
    from app.agents.model_provider import build_planner
    from app.domain.errors import ValidationFailed

    try:
        settings = Settings()
        planner = build_planner(settings)
        proposal = await planner.propose_transaction(intent, {})
    except ValidationFailed as exc:
        print(json.dumps({"error": exc.to_dict(), "applied": False}))
        return 1
    print(json.dumps({"provider": planner.name,
                      "live": planner.name != "deterministic_fixture",
                      "model": getattr(planner, "model", None),
                      "request_id": getattr(planner, "last_request_id", None),
                      "usage": getattr(planner, "last_usage", None),
                      "proposal": proposal.model_dump(mode="json"),
                      "applied": False}, ensure_ascii=False))
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "migrate":
        _migrate()
        return 0
    if cmd in {"create-operator", "create-requester", "create-agent"}:
        return asyncio.run(_provision(cmd, rest))
    if cmd == "scenario":
        return asyncio.run(_scenarios(rest))
    if cmd == "crash-midflight":
        asyncio.run(_crash_midflight())
        return 0
    if cmd == "recover":
        asyncio.run(_recover())
        return 0
    if cmd == "planner-smoke" and len(rest) == 1:
        return asyncio.run(_planner_smoke(rest[0]))
    if cmd in {"model-check", "model-smoke", "model-eval"}:
        return asyncio.run(_model_command(cmd, rest))
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
