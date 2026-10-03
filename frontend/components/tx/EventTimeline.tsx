"use client";

import { useMemo, useState } from "react";
import type { Json, JsonObject, PactEvent, TransactionDetail } from "@/lib/types";
import { fmtTime, money } from "@/lib/format";
import { CATEGORY_CLASSES, eventCategory, isHighlightEvent, type EventCategory } from "@/lib/states";
import { Expandable, JsonView, Mono } from "../ui";

const str = (v: Json | undefined) => (v === undefined || v === null ? "" : typeof v === "string" ? v : JSON.stringify(v));

function summarize(e: PactEvent): string {
  const p = (e.payload ?? {}) as JsonObject;
  switch (e.event_type) {
    case "COMMIT_BARRIER_EVALUATED":
      return p.eligible
        ? "GLOBAL COMMIT ELIGIBLE — all checks passed"
        : `GLOBAL COMMIT BLOCKED: ${((p.blocking_reasons as string[]) ?? []).join(", ")}`;
    case "CHILD_TRANSACTION_CREATED":
      return `delegated to ${str(p.actor_id)}: ${str(p.objective)}`;
    case "CAPABILITY_ISSUED":
    case "CAPABILITY_DELEGATED":
      return `${str(p.subject)} ← ${str(p.issuer ?? p.delegator)} · limit ${money(p.amount_limit as string)} · cumulative ${money(
        p.cumulative_limit as string,
      )}`;
    case "INVARIANT_REGISTERED":
      return `${str(p.key)} (${str(p.phase)})`;
    case "INVARIANT_EVALUATED":
      return `${p.passed ? "✓" : "✗"} ${str(p.key)} [${str(p.context)}] ${str(p.reason)}`;
    case "AUTHORITY_EVALUATED":
      return `${p.passed ? "✓" : "✗"} ${str(p.scope)}`;
    case "EXECUTION_SCHEDULE_EVALUATED": {
      const waiting = Object.keys((p.waiting_on_verification as JsonObject) ?? {}).length;
      return `next: ${str(p.next) || "—"} · ready ${((p.ready as Json[]) ?? []).length} · waiting on verification ${waiting}`;
    }
    case "LOGICAL_OPERATIONS_RESERVED":
      return `${((p.operation_keys as Json[]) ?? []).length} logical operations reserved`;
    case "RECOVERY_DECISION":
      return `recovery action: ${str(p.action)}`;
    case "COMPENSATION_STARTED":
      return `reverse order: ${((p.order as Json[]) ?? []).length} effect(s); irreversible: ${((p.irreversible as Json[]) ?? []).length}`;
    case "COMPENSATION_FINISHED":
      return `complete=${str(p.complete)} · compensated ${((p.compensated as Json[]) ?? []).length} · failed ${((p.failed as Json[]) ?? []).length}`;
    case "TRANSACTION_FINALIZED":
      return `final state ${str(p.final_state)}`;
    case "RECEIPT_CREATED":
      return `receipt ${str(p.receipt_hash).slice(0, 16)}…`;
    case "TRANSACTION_CREATED":
      return `actor ${str(p.actor_id)}`;
  }
  const parts: string[] = [];
  if (p.from !== undefined || p.to !== undefined) parts.push(`${str(p.from)} → ${str(p.to)}`);
  if (p.effect_type) parts.push(str(p.effect_type));
  if (p.http_status !== undefined && p.http_status !== null) parts.push(`HTTP ${str(p.http_status)}`);
  if (p.reason) parts.push(str(p.reason));
  if (p.error) parts.push(`error: ${str(p.error)}`);
  return parts.join(" · ");
}

const CATS: EventCategory[] = ["state", "barrier", "authority", "dispatch", "verification", "reconciliation", "compensation", "receipt"];

export function EventTimeline({ events, detail }: { events: PactEvent[]; detail: TransactionDetail }) {
  const [hidden, setHidden] = useState<Set<EventCategory>>(new Set());
  const [onlyKey, setOnlyKey] = useState(false);
  const actorByTx = useMemo(() => new Map(detail.tree.map((n) => [n.id, n.actor_id])), [detail.tree]);
  const shown = events.filter(
    (e) => !hidden.has(eventCategory(e.event_type)) && (!onlyKey || isHighlightEvent(e.event_type) || e.event_type.includes("FAIL") || e.event_type.includes("REJECT")),
  );

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
        {CATS.map((c) => (
          <button
            key={c}
            type="button"
            onClick={() =>
              setHidden((h) => {
                const n = new Set(h);
                if (n.has(c)) n.delete(c);
                else n.add(c);
                return n;
              })
            }
            aria-pressed={!hidden.has(c)}
            className={`neu-btn !rounded-lg border-l-4 px-2.5 py-1 ${CATEGORY_CLASSES[c]} ${hidden.has(c) ? "opacity-60" : ""}`}
          >
            {c}
          </button>
        ))}
        <label className="ml-2 flex items-center gap-1 text-mute">
          <input type="checkbox" checked={onlyKey} onChange={(e) => setOnlyKey(e.target.checked)} /> key events only
        </label>
        <span className="ml-auto text-faint">
          {shown.length}/{events.length} durable events
        </span>
      </div>
      <div className="neu-inset max-h-[640px] overflow-y-auto">
        <table className="w-full text-xs">
          <tbody>
            {shown.map((e) => {
              const cat = eventCategory(e.event_type);
              const hl = isHighlightEvent(e.event_type);
              const p = e.payload as JsonObject;
              return (
                <tr
                  key={e.sequence}
                  className={`border-b border-line align-top ${hl ? "bg-white/50" : ""}`}
                >
                  <td className={`w-14 border-l-4 py-1 pl-2 font-mono text-faint ${CATEGORY_CLASSES[cat]}`}>{e.sequence}</td>
                  <td className="w-24 py-1 font-mono text-faint">{fmtTime(e.created_at)}</td>
                  <td className="py-1 pr-2">
                    <div className={`font-mono font-semibold ${CATEGORY_CLASSES[cat].split(" ")[0]} ${hl ? "underline decoration-2 underline-offset-2" : ""}`}>
                      {e.event_type}
                    </div>
                    <div className="text-xs text-faint">
                      {e.actor}
                      {actorByTx.get(e.transaction_id) && actorByTx.get(e.transaction_id) !== e.actor && (
                        <> · tx {actorByTx.get(e.transaction_id)}</>
                      )}
                    </div>
                  </td>
                  <td className="py-1 pr-2">
                    <div className="text-ink-soft">{summarize(e)}</div>
                    {typeof p?.operation_key === "string" && <Mono className="text-xs text-faint">{p.operation_key}</Mono>}
                    <Expandable label="payload">
                      <JsonView value={e.payload} maxHeight={220} />
                    </Expandable>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {events.length === 0 && <div className="p-4 text-sm text-faint">Waiting for events…</div>}
      </div>
    </div>
  );
}
