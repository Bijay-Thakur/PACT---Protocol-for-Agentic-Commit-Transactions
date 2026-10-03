"use client";

import type { Invariant, TransactionDetail } from "@/lib/types";
import { fmtTime } from "@/lib/format";
import { Expandable, JsonView, Mono, PassMark } from "../ui";

const PHASES = ["PREPARE", "PRE_COMMIT", "POST_EXECUTION", "FINAL"];

export function InvariantsPanel({ detail }: { detail: TransactionDetail }) {
  const phases = [...PHASES, ...new Set(detail.invariants.map((i) => i.phase).filter((p) => !PHASES.includes(p)))];
  if (!detail.invariants.length) return <div className="text-sm text-faint">No invariants registered.</div>;
  return (
    <div className="space-y-4">
      {phases.map((phase) => {
        const list = detail.invariants.filter((i) => i.phase === phase);
        if (!list.length) return null;
        return (
          <div key={phase}>
            <div className="mb-1.5 font-mono text-xs font-semibold tracking-widest text-mute">{phase}</div>
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
  const border = latest && !latest.passed ? "border-l-4 border-red-600" : "";
  return (
    <div className={`neu-raised-md p-4 ${border}`}>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <PassMark passed={latest ? latest.passed : null} />
        <span className="font-semibold text-ink">{inv.name}</span>
        <Mono className="text-faint">{inv.key}</Mono>
        <span className="ml-auto flex items-center gap-2 text-xs">
          <Mono className="text-faint">{inv.expression_type}</Mono>
          <Mono className="neu-tag px-2 py-0.5 text-ink-soft">on fail: {inv.failure_action}</Mono>
          {inv.severity && <Mono className="text-faint">{inv.severity}</Mono>}
        </span>
      </div>
      {latest ? (
        <div className={`mt-1 text-xs ${latest.passed ? "text-mute" : "text-bad"}`}>
          latest ({latest.context}): {latest.reason}
        </div>
      ) : (
        <div className="mt-1 text-xs text-faint">not evaluated yet</div>
      )}
      {inv.evaluations.length > 0 && (
        <div className="mt-2 space-y-1">
          {inv.evaluations.map((ev, i) => (
            <div key={i} className="neu-inset-sm px-3 py-1.5">
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <PassMark passed={ev.passed} />
                <Mono className="text-ink-soft">{ev.context}</Mono>
                <span className="text-mute">{ev.reason}</span>
                <Mono className="ml-auto text-faint">{fmtTime(ev.at)}</Mono>
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
