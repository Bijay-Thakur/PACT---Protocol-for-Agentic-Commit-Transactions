"use client";

import type { BarrierCheck, CommitDecision, Json, TransactionDetail } from "@/lib/types";
import { fmtTime } from "@/lib/format";
import { Card, Expandable, JsonView, Mono, PassMark } from "../ui";
import { ExposureBar, type Contribution } from "./ExposureBar";

function exposureOf(observed: Json | undefined) {
  if (!observed || typeof observed !== "object" || Array.isArray(observed)) return null;
  const o = observed as Record<string, Json>;
  if (o.exposure === undefined || !Array.isArray(o.contributions)) return null;
  return {
    exposure: String(o.exposure),
    limit: o.limit === null || o.limit === undefined ? null : String(o.limit),
    contributions: o.contributions as unknown as Contribution[],
  };
}

export function CommitBarrierPanel({ detail }: { detail: TransactionDetail }) {
  const binding = detail.commit_decisions[0] ?? null;
  const decision: CommitDecision | null = binding ?? detail.dry_run_decision;
  const isDryRun = !binding && !!decision;

  if (!decision) {
    return (
      <Card title="Global commit barrier">
        <div className="text-sm text-zinc-500">No barrier evaluation recorded yet.</div>
      </Card>
    );
  }

  const failed = decision.checks.filter((c) => !c.passed);
  const verdictCls = decision.eligible
    ? "border-emerald-600 bg-emerald-950/40 text-emerald-300"
    : "border-red-600 bg-red-950/40 text-red-300";

  return (
    <Card
      title="Global commit barrier"
      subtitle={
        isDryRun
          ? "dry-run evaluation (no binding decision recorded yet)"
          : `binding decision · snapshot v${decision.snapshot_version ?? "?"} · ${fmtTime(decision.evaluated_at)}${
              detail.commit_decisions.length > 1 ? ` · ${detail.commit_decisions.length} decisions recorded` : ""
            }`
      }
      right={
        isDryRun ? (
          <span className="rounded bg-zinc-800 px-2 py-0.5 font-mono text-[10px] uppercase text-zinc-300">dry-run</span>
        ) : null
      }
    >
      <div className={`rounded-md border-2 px-4 py-3 ${verdictCls}`}>
        <div className="text-xs uppercase tracking-widest opacity-80">Global commit</div>
        <div className="text-2xl font-bold tracking-wide">{decision.eligible ? "ELIGIBLE" : "BLOCKED"}</div>
        <div className="mt-1 text-xs text-zinc-300">
          {decision.checks.length - failed.length}/{decision.checks.length} checks passed
          {decision.blocking_reasons.length > 0 && (
            <>
              {" · "}blocking:{" "}
              {decision.blocking_reasons.map((r) => (
                <Mono key={r} className="mr-2 rounded bg-red-900/60 px-1.5 py-0.5 text-red-100">
                  {r}
                </Mono>
              ))}
            </>
          )}
        </div>
      </div>

      {/* Highlight exposure-type failed checks first (e.g. GLOBAL_BUDGET) */}
      {decision.checks
        .filter((c) => exposureOf(c.observed) && (!c.passed || c.code === "GLOBAL_BUDGET"))
        .map((c, i) => {
          const ex = exposureOf(c.observed)!;
          return (
            <div key={`ex-${i}`} className={`mt-3 rounded-md border p-3 pt-5 ${c.passed ? "border-zinc-800" : "border-red-800 bg-red-950/20"}`}>
              <div className="mb-3 flex items-center gap-2 text-xs">
                <PassMark passed={c.passed} />
                <Mono className="font-semibold text-zinc-100">{c.code}</Mono>
                <span className="text-zinc-400">{c.subject}</span>
              </div>
              <ExposureBar contributions={ex.contributions} exposure={ex.exposure} limit={ex.limit} />
            </div>
          );
        })}

      <div className="mt-3 max-h-[380px] overflow-y-auto pr-1">
        <table className="w-full text-xs">
          <tbody>
            {decision.checks.map((c, i) => (
              <CheckRow key={i} c={c} />
            ))}
          </tbody>
        </table>
      </div>
      {decision.explanation && <p className="mt-3 text-xs text-zinc-400">{decision.explanation}</p>}
    </Card>
  );
}

function CheckRow({ c }: { c: BarrierCheck }) {
  const hasObserved = c.observed && typeof c.observed === "object" && Object.keys(c.observed as object).length > 0;
  return (
    <tr className={`border-b border-zinc-800/70 align-top ${c.passed ? "" : "bg-red-950/40"}`}>
      <td className="w-5 py-1.5 pl-1">
        <PassMark passed={c.passed} />
      </td>
      <td className="py-1.5 pr-2">
        <Mono className={c.passed ? "text-zinc-300" : "font-semibold text-red-200"}>{c.code}</Mono>
        {c.subject && <div className="font-mono text-[10px] text-zinc-500 break-all">{c.subject}</div>}
      </td>
      <td className="py-1.5">
        <div className={c.passed ? "text-zinc-400" : "text-red-100"}>{c.detail}</div>
        {c.blocking_reason && (
          <Mono className="mt-0.5 inline-block rounded bg-red-900/70 px-1.5 text-[11px] text-red-100">{c.blocking_reason}</Mono>
        )}
        {hasObserved && (
          <Expandable label="observed">
            <JsonView value={c.observed} maxHeight={180} />
          </Expandable>
        )}
      </td>
    </tr>
  );
}
