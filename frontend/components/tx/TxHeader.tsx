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
  const [operatorId, setOperatorId] = useState("operator-1");
  const [note, setNote] = useState("");

  async function act(label: string, fn: () => Promise<{ state: string; explanation?: string }>) {
    setBusy(label);
    setResult(null);
    try {
      const r = await fn();
      setResult({ ok: true, text: `${label}: transaction is now ${r.state}${r.explanation ? ` — ${r.explanation}` : ""}` });
    } catch (e) {
      const err = e as ApiError;
      setResult({ ok: false, text: `${label} failed: ${err.code ?? ""} ${err.message}` });
    } finally {
      setBusy(null);
      onChanged();
    }
  }

  const op = (action: OperatorActionType, label: string) => () =>
    act(label, () => api.operatorAction(tx.id, { operator_id: operatorId, action, note }));

  const btn =
    "rounded px-3 py-1.5 text-xs font-semibold disabled:opacity-50 disabled:cursor-not-allowed";

  return (
    <section className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-4">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-3">
            <StateBadge state={tx.state} size="lg" />
            <span className="text-xs text-zinc-500">root transaction</span>
            <LiveIndicator mode={mode} lastUpdate={lastUpdate} />
          </div>
          <h1 className="mt-2 text-lg font-semibold leading-snug text-zinc-50">{tx.objective}</h1>
          <div className="mt-2 flex flex-wrap gap-x-6 gap-y-1 text-xs text-zinc-400">
            <span>
              root <Mono className="text-zinc-200">{tx.id}</Mono>
            </span>
            <span>
              customer <Mono className="text-zinc-200">{detail.metadata.customer_id ?? "—"}</Mono>
            </span>
            <span>
              scenario <Mono className="text-zinc-200">{detail.metadata.scenario ?? "—"}</Mono>
            </span>
            <span>
              actor <Mono className="text-zinc-200">{tx.actor_id}</Mono>
            </span>
            <span>events {detail.event_count}</span>
            <span>
              policy <Mono className="text-zinc-300">
                on_unknown={detail.policy.on_unknown} · on_failure={detail.policy.on_effect_failure} · on_comp_failure=
                {detail.policy.on_compensation_failure}
              </Mono>
            </span>
          </div>
        </div>
        <div className="flex flex-col items-end gap-2">
          <Link
            href={`/tx/${tx.id}/receipt`}
            className="rounded border border-teal-700 px-3 py-1.5 text-xs font-semibold text-teal-300 hover:bg-teal-900/30"
          >
            {tx.receipt_hash ? "View receipt" : "View receipt (draft)"}
          </Link>
        </div>
      </div>

      {/* State-dependent actions */}
      {tx.state === "PREPARED" && (
        <div className="mt-4 flex items-center gap-3 rounded-md border border-zinc-700 bg-zinc-950/60 p-3">
          <button
            className={`${btn} bg-indigo-600 text-white hover:bg-indigo-500`}
            disabled={!!busy}
            onClick={() => act("Commit", () => api.commit(tx.id, { step_delay_ms: 400, background: true }))}
          >
            {busy === "Commit" ? "Committing…" : "Commit"}
          </button>
          <span className="text-xs text-zinc-400">
            Evaluates the global commit barrier; execution proceeds only if every check passes.
          </span>
        </div>
      )}
      {tx.state === "UNKNOWN" && (
        <div className="mt-4 flex items-center gap-3 rounded-md border border-amber-700/60 bg-amber-950/30 p-3">
          <button
            className={`${btn} bg-amber-600 text-black hover:bg-amber-500`}
            disabled={!!busy}
            onClick={() => act("Reconcile", () => api.reconcile(tx.id))}
          >
            {busy === "Reconcile" ? "Reconciling…" : "Reconcile"}
          </button>
          <span className="text-xs text-amber-200">
            PACT will query the provider by operation identity — it will NOT re-send the refund.
          </span>
        </div>
      )}
      {tx.state === "HUMAN_REQUIRED" && (
        <div className="mt-4 rounded-md border border-orange-700/60 bg-orange-950/30 p-3">
          <div className="mb-2 text-xs text-orange-200">
            PACT could not reach a safe terminal state automatically. An accountable operator must decide; every action is
            recorded on the receipt.
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <input
              value={operatorId}
              onChange={(e) => setOperatorId(e.target.value)}
              className="w-32 rounded border border-zinc-700 bg-zinc-900 px-2 py-1 font-mono text-xs"
              placeholder="operator id"
            />
            <input
              value={note}
              onChange={(e) => setNote(e.target.value)}
              className="min-w-[240px] flex-1 rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs"
              placeholder="note (recorded on the receipt)"
            />
            <button className={`${btn} bg-sky-700 text-white hover:bg-sky-600`} disabled={!!busy || !operatorId}
              onClick={op("RETRY_COMPENSATION", "Retry compensation")}>
              Retry compensation
            </button>
            <button className={`${btn} bg-amber-700 text-white hover:bg-amber-600`} disabled={!!busy || !operatorId}
              onClick={op("RETRY_RECONCILIATION", "Retry reconciliation")}>
              Retry reconciliation
            </button>
            <button className={`${btn} bg-red-700 text-white hover:bg-red-600`} disabled={!!busy || !operatorId}
              onClick={op("FINALIZE_FAILED", "Finalize as failed")}>
              Finalize as failed
            </button>
          </div>
        </div>
      )}
      {result && (
        <div className={`mt-3 rounded px-3 py-2 text-xs ${result.ok ? "bg-zinc-800 text-zinc-200" : "bg-red-950/60 text-red-200"}`}>
          {result.text}
        </div>
      )}
    </section>
  );
}

function LiveIndicator({ mode, lastUpdate }: { mode: LiveMode; lastUpdate: number | null }) {
  const label = mode === "sse" ? "live (SSE)" : mode === "polling" ? "polling 2s" : "connecting";
  const color = mode === "sse" ? "bg-emerald-400" : mode === "polling" ? "bg-amber-400" : "bg-zinc-500";
  return (
    <span className="flex items-center gap-1.5 text-[11px] text-zinc-500">
      <span className={`h-2 w-2 rounded-full ${color} ${mode === "sse" ? "animate-pulse" : ""}`} />
      {label}
      {lastUpdate && <span className="font-mono">· updated {fmtTime(new Date(lastUpdate).toISOString())}</span>}
    </span>
  );
}
