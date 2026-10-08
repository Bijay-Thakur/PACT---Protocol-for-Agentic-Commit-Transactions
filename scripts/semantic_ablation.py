"""Local ablation of the deterministic guard and judge. Not a human-qualified score.

Generator labels are an oracle for this engineering measurement only. They do not
satisfy the human adjudication gate. Repeating the same 40 cases measures
instability of the deterministic path; repeats are not added to the denominator.
"""

from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

from app.agents.model_provider import PlanProposal
from app.agents.semantic_judge import DeterministicSemanticJudge
from app.domain.semantics import compare_intent_to_proposal, extract_intent_semantics

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "backend" / "tests" / "fixtures" / "semantic_intents_v1.jsonl"


def reduced_proposal(intent: str) -> PlanProposal:
    semantics = extract_intent_semantics(intent)
    customer = semantics.entity_identifiers.get("customer_id", "")
    return PlanProposal(
        objective="Cancel the customer",
        requested_workflow="customer_offboarding",
        entity_references={"customer_id": customer} if customer else {},
        candidate_actions=["cancel_subscription"],
    )


def echoed_proposal(intent: str) -> PlanProposal:
    semantics = extract_intent_semantics(intent)
    customer = semantics.entity_identifiers.get("customer_id", "")
    return PlanProposal(
        objective=intent[:2000],
        requested_workflow=semantics.workflow_id or "customer_offboarding",
        entity_references={"customer_id": customer} if customer else {},
        candidate_actions=["cancel_subscription", "revoke_premium", "mark_churned",
                           "refund_unused", "confirm_customer"],
    )


def held(issues) -> bool:
    return any(issue.severity == "CRITICAL" for issue in issues)


async def main() -> None:
    import asyncio
    cases = [json.loads(line) for line in CORPUS.read_text(encoding="utf-8").splitlines() if line.strip()]
    judge = DeterministicSemanticJudge()
    modes = {"deterministic_reduced": 0, "proposer_echo": 0, "proposer_plus_judge": 0}
    clean_pass = critical_hold = 0
    clean = critical = 0
    started = time.perf_counter()
    for case in cases:
        intent = case["intent"]
        reduced = compare_intent_to_proposal(extract_intent_semantics(intent), reduced_proposal(intent))
        echoed = compare_intent_to_proposal(extract_intent_semantics(intent), echoed_proposal(intent))
        modes["deterministic_reduced"] += held(reduced)
        modes["proposer_echo"] += held(echoed)
        assessment = await judge.assess(
            source_text=intent, accepted=extract_intent_semantics(intent),
            plan={"effects": [{"slot": "cancel_subscription"}]}, candidate_digest="a" * 64,
        )
        judged_hold = assessment.aggregate != "PASS" or held(echoed)
        modes["proposer_plus_judge"] += judged_hold
        if case["split"] == "qualification" and not case["critical"]:
            clean += 1
            clean_pass += assessment.aggregate == "PASS" and not held(echoed)
        if case["split"] == "qualification" and case["critical"]:
            critical += 1
            critical_hold += judged_hold
    elapsed = time.perf_counter() - started
    subset = cases[:40]
    repeats = []
    for _ in range(3):
        sample_started = time.perf_counter()
        signature = []
        for case in subset:
            issues = compare_intent_to_proposal(extract_intent_semantics(case["intent"]), reduced_proposal(case["intent"]))
            signature.append(tuple(sorted(issue.code for issue in issues)))
        repeats.append({"seconds": round(time.perf_counter() - sample_started, 4), "signature": signature})
    stable = repeats[0]["signature"] == repeats[1]["signature"] == repeats[2]["signature"]
    report = {
        "oracle": "generator_labels_not_human",
        "cases": len(cases),
        "holds": modes,
        "qualification_clean_judge_pass": {"count": clean_pass, "denominator": clean},
        "qualification_critical_held": {"count": critical_hold, "denominator": critical},
        "always_hold": modes["proposer_plus_judge"] == len(cases),
        "repetition": {
            "subset": 40, "runs": 3, "stable": stable,
            "seconds": [item["seconds"] for item in repeats],
            "independent_denominator_inflated": False,
        },
        "latency_seconds": {
            "all_cases": round(elapsed, 4),
            "mean_case": round(elapsed / len(cases), 6),
            "p50_repeat_subset": statistics.median(item["seconds"] for item in repeats),
        },
        "cost": {"provider_calls": 0, "tokens": 0, "reason": "deterministic local ablation"},
        "human_qualified": False,
    }
    out = ROOT / ".local" / "semantic-ablation.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "repetition"} | {
        "repetition_stable": stable, "repetition_runs": 3, "repetition_subset": 40,
    }))


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
