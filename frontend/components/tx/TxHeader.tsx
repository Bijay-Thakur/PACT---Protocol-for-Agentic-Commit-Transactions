"use client";

import { useState } from "react";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import type { OperatorActionType, TransactionDetail } from "@/lib/types";
import type { LiveMode } from "@/lib/useTransactionLive";
import { fmtTime } from "@/lib/format";
import { Mono, StateBadge } from "../ui";

export function TxHeader({
  detail,
  mode,
  lastUpdate,
  onChanged,
}: {
  detail: TransactionDetail;
  mode: LiveMode;
  lastUpdate: number | null;
  onChanged: () => void;
}) {
  const tx = detail.transaction;
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const [note, setNote] = useState("");

  async function act(label: string, fn: () => Promise<{ state?: string; status?: string }>) {
    setBusy(label);
    setResult(null);
    try {
      const r = await fn();
      setResult({ ok: true, text: `${label}: ${r.state || r.status || "recorded"}` });
    } catch (e) {
      const err = e as ApiError;
      setResult({ ok: false, text: `${label} failed: ${err.code ?? ""} ${err.message}` });
    } finally {
      setBusy(null);
      onChanged();
    }
  }

  const op = (action: OperatorActionType, label: string) => () =>
    act(label, () => api.operatorAction(tx.id, { action, reason: note }));

  const btn =
    "neu-btn px-4 py-2 text-xs";

  return (
    <section className="neu-raised p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-3">
            <StateBadge state={tx.state} size="lg" />
            <span className="text-xs text-faint">root transaction</span>
            <LiveIndicator mode={mode} lastUpdate={lastUpdate} />
          </div>
          <h1 className="mt-2 text-lg font-semibold leading-snug text-ink">{tx.objective}</h1>
          <div className="mt-2 flex flex-wrap gap-x-6 gap-y-1 text-xs text-mute">
            <span>
              root <Mono className="text-ink">{tx.id}</Mono>
            </span>
            <span>
              customer <Mono className="text-ink">{detail.metadata.customer_id ?? "—"}</Mono>
            </span>
            <span>
              scenario <Mono className="text-ink">{detail.metadata.scenario ?? "—"}</Mono>
            </span>
            <span>
              actor <Mono className="text-ink">{tx.actor_id}</Mono>
            </span>
            <span>events {detail.event_count}</span>
            <span>
              policy <Mono className="text-ink-soft">
                on_unknown={detail.policy.on_unknown} · on_failure={detail.policy.on_effect_failure} · on_comp_failure=
                {detail.policy.on_compensation_failure}
              </Mono>
            </span>
          </div>
        </div>
        <div className="flex flex-col items-end gap-2">
          <Link
            href={`/tx/${tx.id}/receipt`}
            className="neu-btn px-4 py-2 text-xs text-evidence"
          >
            {tx.receipt_hash ? "View receipt" : "View receipt (draft)"}
          </Link>
        </div>
      </div>

      {/* State-dependent actions */}
      {tx.state === "PREPARED" && (
        <div className="neu-inset mt-5 flex items-center gap-4 p-4">
          {detail.plan_revision?.approval_required && detail.plan_revision.digest && <>
            <input value={note} onChange={(e) => setNote(e.target.value)}
              className="neu-field min-w-[240px] px-3 py-2 text-xs" placeholder="Approval reason" />
            <button className={`${btn} neu-btn-primary`} disabled={!!busy || note.length < 3}
              onClick={() => act("Approve", () => api.approve(tx.id, detail.plan_revision!.digest!, note))}>
              Approve frozen plan
            </button>
          </>}
          <span className="text-xs text-mute">
            Digest {detail.plan_revision?.digest?.slice(0, 16) ?? "unavailable"}. The initiating agent requests
            commit with the complete digest after review.
          </span>
        </div>
      )}
      {tx.state === "UNKNOWN" && (
        <div className="neu-inset mt-5 flex items-center gap-4 border-l-4 border-amber-700 p-4">
          <button
            className={`${btn} text-warn`}
            disabled={!!busy}
            onClick={() => act("Reconcile", () => api.reconcile(tx.id, "Operator requested reconciliation"))}
          >
            {busy === "Reconcile" ? "Reconciling…" : "Reconcile"}
          </button>
          <span className="text-xs text-warn">
            PACT will query the provider by operation identity — it will NOT re-send the refund.
          </span>
        </div>
      )}
      {tx.state === "HUMAN_REQUIRED" && (
        <div className="neu-inset mt-5 border-l-4 border-orange-700 p-4">
          <div className="mb-2 text-xs text-escalate">
            PACT could not reach a safe terminal state automatically. An accountable operator must decide; every action is
            recorded on the receipt.
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <input
              value={note}
              onChange={(e) => setNote(e.target.value)}
              className="neu-field min-w-[240px] flex-1 px-3 py-2 text-xs"
              placeholder="note (recorded on the receipt)"
            />
            <button className={`${btn} text-info`} disabled={!!busy || note.length < 3}
              onClick={op("RETRY_RESTORATION", "Retry restoration")}>
              Retry restoration
            </button>
            <button className={`${btn} text-warn`} disabled={!!busy || note.length < 3}
              onClick={op("RECONCILE", "Retry reconciliation")}>
              Retry reconciliation
            </button>
            <button className={`${btn} text-bad`} disabled={!!busy || note.length < 3}
              onClick={op("FINALIZE_FAILED", "Finalize as failed")}>
              Finalize as failed
            </button>
          </div>
        </div>
      )}
      {result && (
        <div className={`neu-inset-sm mt-4 px-4 py-2.5 text-xs ${result.ok ? "text-ink" : "border-l-4 border-red-600 text-bad"}`}>
          {result.text}
        </div>
      )}
    </section>
  );
}

function LiveIndicator({ mode, lastUpdate }: { mode: LiveMode; lastUpdate: number | null }) {
  const label = mode === "sse" ? "live (SSE)" : mode === "polling" ? "polling 2s" : "connecting";
  const color = mode === "sse" ? "bg-emerald-600" : mode === "polling" ? "bg-amber-700" : "bg-slate-500";
  return (
    <span className="flex items-center gap-1.5 text-xs text-faint">
      <span className={`h-2 w-2 rounded-full ${color} ${mode === "sse" ? "animate-pulse" : ""}`} />
      {label}
      {lastUpdate && <span className="font-mono">· updated {fmtTime(new Date(lastUpdate).toISOString())}</span>}
    </span>
  );
}
