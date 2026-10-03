"use client";

import { useMemo } from "react";
import type { PactEvent, TransactionDetail, TreeNode } from "@/lib/types";
import { fmtTime, money, shortId } from "@/lib/format";
import { Card, Mono, StateBadge } from "../ui";

/** Transaction ids that reached PREPARED locally, derived from durable events. */
export function locallyPrepared(events: PactEvent[]) {
  const m = new Map<string, string>();
  for (const e of events) {
    if (e.event_type === "TRANSACTION_STATE_CHANGED" && e.payload?.to === "PREPARED" && !m.has(e.transaction_id)) {
      m.set(e.transaction_id, e.created_at);
    }
  }
  return m;
}

export function HierarchyPanel({ detail, events }: { detail: TransactionDetail; events: PactEvent[] }) {
  const prepared = useMemo(() => locallyPrepared(events), [events]);
  const decision = detail.commit_decisions[0] ?? detail.dry_run_decision;
  const byParent = useMemo(() => {
    const m = new Map<string | null, TreeNode[]>();
    for (const n of detail.tree) {
      const k = n.parent_id;
      m.set(k, [...(m.get(k) ?? []), n]);
    }
    return m;
  }, [detail.tree]);
  const root = detail.tree.find((n) => n.parent_id === null) ?? detail.tree[0];
  const children = detail.tree.filter((n) => n.parent_id !== null);
  const preparedCount = children.filter((c) => prepared.has(c.id)).length;

  return (
    <Card
      title="Agent hierarchy · local vs global validity"
      subtitle="Each child agent prepares its own sub-transaction. Local PREPARED does not authorize execution — only the global barrier does."
    >
      <div className="mb-3 grid grid-cols-2 gap-2 text-xs">
        <div className="rounded border border-zinc-700 bg-zinc-950/50 px-3 py-2">
          <div className="text-zinc-500">children locally prepared</div>
          <div className="text-lg font-semibold text-zinc-100">
            {preparedCount}/{children.length}
          </div>
        </div>
        <div
          className={`rounded border px-3 py-2 ${
            !decision
              ? "border-zinc-700"
              : decision.eligible
                ? "border-emerald-700 bg-emerald-950/30"
                : "border-red-700 bg-red-950/30"
          }`}
        >
          <div className="text-zinc-500">global barrier verdict{detail.commit_decisions.length ? "" : " (dry-run)"}</div>
          <div className={`text-lg font-semibold ${!decision ? "text-zinc-400" : decision.eligible ? "text-emerald-300" : "text-red-300"}`}>
            {!decision ? "not evaluated" : decision.eligible ? "ELIGIBLE" : "BLOCKED"}
          </div>
          {decision && decision.blocking_reasons.length > 0 && (
            <div className="font-mono text-[10px] text-red-200">{decision.blocking_reasons.join(" · ")}</div>
          )}
        </div>
      </div>
      {root && <Node node={root} byParent={byParent} prepared={prepared} detail={detail} />}
    </Card>
  );
}

function Node({
  node,
  byParent,
  prepared,
  detail,
}: {
  node: TreeNode;
  byParent: Map<string | null, TreeNode[]>;
  prepared: Map<string, string>;
  detail: TransactionDetail;
}) {
  const kids = byParent.get(node.id) ?? [];
  const effects = detail.effects.filter((e) => e.transaction_id === node.id);
  const preparedAt = prepared.get(node.id);
  return (
    <div className={node.depth > 0 ? "ml-4 border-l border-zinc-700 pl-3" : ""}>
      <div className="flex flex-wrap items-center gap-2 py-1 text-xs">
        <Mono className="font-semibold text-zinc-100">{node.actor_id}</Mono>
        <StateBadge state={node.state} size="xs" />
        {preparedAt ? (
          <span className="rounded bg-emerald-900/40 px-1.5 text-[10px] text-emerald-300" title={`PREPARED at ${fmtTime(preparedAt)}`}>
            locally prepared ✓
          </span>
        ) : (
          <span className="rounded bg-zinc-800 px-1.5 text-[10px] text-zinc-500">not locally prepared</span>
        )}
        {!node.required && <span className="text-[10px] text-zinc-500">optional</span>}
        {node.required && node.depth > 0 && <span className="text-[10px] text-zinc-500">required</span>}
        <span className="text-[10px] text-zinc-500">
          {node.effect_count} effect{node.effect_count === 1 ? "" : "s"}
        </span>
        <Mono className="text-[10px] text-zinc-600">{shortId(node.id)}</Mono>
      </div>
      {effects.length > 0 && (
        <div className="mb-1 ml-1 flex flex-wrap gap-1.5">
          {effects.map((e) => (
            <span key={e.id} className="font-mono text-[10px] text-zinc-400">
              ↳ {e.effect_type}
              {e.amount ? ` ${money(e.amount)}` : ""}
            </span>
          ))}
        </div>
      )}
      {kids.map((k) => (
        <Node key={k.id} node={k} byParent={byParent} prepared={prepared} detail={detail} />
      ))}
    </div>
  );
}
