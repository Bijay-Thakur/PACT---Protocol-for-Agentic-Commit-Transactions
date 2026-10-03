"use client";

import type { Invariant, TransactionDetail } from "@/lib/types";
import { fmtTime } from "@/lib/format";
import { Expandable, JsonView, Mono, PassMark } from "../ui";

const PHASES = ["PREPARE", "PRE_COMMIT", "POST_EXECUTION", "FINAL"];

export function InvariantsPanel({ detail }: { detail: TransactionDetail }) {
  const phases = [...PHASES, ...new Set(detail.invariants.map((i) => i.phase).filter((p) => !PHASES.includes(p)))];
  if (!detail.invariants.length) return <div className="text-sm text-zinc-500">No invariants registered.</div>;
  return (
    <div className="space-y-4">
      {phases.map((phase) => {
        const list = detail.invariants.filter((i) => i.phase === phase);
        if (!list.length) return null;
        return (
          <div key={phase}>
            <div className="mb-1.5 font-mono text-[11px] font-semibold tracking-widest text-zinc-400">{phase}</div>
            <div className="space-y-2">
              {list.map((inv) => (
                <InvariantRow key={inv.id} inv={inv} />
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function InvariantRow({ inv }: { inv: Invariant }) {
  const latest = inv.evaluations[inv.evaluations.length - 1] ?? null;
  const border = !latest ? "border-zinc-800" : latest.passed ? "border-zinc-800" : "border-red-700 bg-red-950/20";
  return (
    <div className={`rounded-md border p-3 ${border}`}>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <PassMark passed={latest ? latest.passed : null} />
        <span className="font-semibold text-zinc-100">{inv.name}</span>
        <Mono className="text-zinc-500">{inv.key}</Mono>
        <span className="ml-auto flex items-center gap-2 text-[10px]">
          <Mono className="text-zinc-500">{inv.expression_type}</Mono>
          <Mono className="rounded bg-zinc-800 px-1.5 text-zinc-300">on fail: {inv.failure_action}</Mono>
          {inv.severity && <Mono className="text-zinc-500">{inv.severity}</Mono>}
        </span>
      </div>
      {latest ? (
        <div className={`mt-1 text-xs ${latest.passed ? "text-zinc-400" : "text-red-200"}`}>
          latest ({latest.context}): {latest.reason}
        </div>
      ) : (
        <div className="mt-1 text-xs text-zinc-500">not evaluated yet</div>
      )}
      {inv.evaluations.length > 0 && (
        <div className="mt-2 space-y-1">
          {inv.evaluations.map((ev, i) => (
            <div key={i} className="rounded bg-zinc-950/50 px-2 py-1">
              <div className="flex flex-wrap items-center gap-2 text-[11px]">
                <PassMark passed={ev.passed} />
                <Mono className="text-zinc-300">{ev.context}</Mono>
                <span className="text-zinc-400">{ev.reason}</span>
                <Mono className="ml-auto text-zinc-600">{fmtTime(ev.at)}</Mono>
              </div>
              <Expandable label="observed values">
                <JsonView value={ev.observed_values} maxHeight={200} />
              </Expandable>
            </div>
          ))}
        </div>
      )}
      {Object.keys(inv.config ?? {}).length > 0 && (
        <Expandable label="config">
          <JsonView value={inv.config} maxHeight={140} />
        </Expandable>
      )}
    </div>
  );
}
