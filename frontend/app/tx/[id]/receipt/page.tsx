"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import type { ReceiptPayload, ReceiptVerify } from "@/lib/types";
import { fmtTime, money, shortId } from "@/lib/format";
import { stateClasses } from "@/lib/states";
import { Card, ErrorBox, Expandable, JsonView, Mono, PassMark, StateBadge } from "@/components/ui";

type Loaded =
  | { kind: "final"; sha256: string; finalState: string; createdAt: string; payload: ReceiptPayload }
  | { kind: "draft"; state: string; payload: ReceiptPayload };

async function fetchReceipt(id: string): Promise<Loaded | ApiError> {
  try {
    const r = await api.getReceipt(id);
    return { kind: "final", sha256: r.sha256, finalState: r.final_state, createdAt: r.created_at, payload: r.payload };
  } catch (e) {
    const err = e instanceof ApiError ? e : new ApiError(0, "UNKNOWN", String(e), null);
    if (err.status === 409 && err.code === "RECEIPT_NOT_FINAL" && err.details?.draft) {
      return { kind: "draft", state: String(err.details.state ?? ""), payload: err.details.draft as ReceiptPayload };
    }
    return err;
  }
}

export default function ReceiptPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id;
  const [data, setData] = useState<Loaded | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [verify, setVerify] = useState<ReceiptVerify | null>(null);
  const [verifyErr, setVerifyErr] = useState<string | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [raw, setRaw] = useState(false);

  const apply = useCallback((r: Loaded | ApiError) => {
    if (r instanceof ApiError) setError(r);
    else {
      setData(r);
      setError(null);
    }
  }, []);

  useEffect(() => {
    if (!id) return;
    let alive = true;
    fetchReceipt(id).then((r) => alive && apply(r));
    return () => {
      alive = false;
    };
  }, [id, apply]);

  // Drafts change as the transaction progresses; refresh until final.
  useEffect(() => {
    if (!id || data?.kind !== "draft") return;
    const t = setInterval(() => void fetchReceipt(id).then(apply), 3000);
    return () => clearInterval(t);
  }, [id, data?.kind, apply]);

  async function doVerify() {
    if (!id) return;
    setVerifying(true);
    setVerifyErr(null);
    try {
      setVerify(await api.verifyReceipt(id));
    } catch (e) {
      setVerifyErr(`${(e as ApiError).code}: ${(e as ApiError).message}`);
    } finally {
      setVerifying(false);
    }
  }

  const back = (
    <Link href={`/tx/${id}`} className="text-xs text-zinc-400 hover:text-zinc-200">
      ← transaction {shortId(id)}
    </Link>
  );

  if (error) {
    return (
      <div className="space-y-3">
        {back}
        <ErrorBox title="Cannot load receipt" message={`${error.code}: ${error.message}`} />
      </div>
    );
  }
  if (!data) return <div className="text-sm text-zinc-500">Loading receipt…</div>;

  const p = data.payload;
  const finalState = data.kind === "final" ? data.finalState : data.state;
  const uncompensated = p.uncompensated_effects ?? [];

  return (
    <div className="space-y-4">
      {back}
      {data.kind === "draft" && (
        <div className="rounded-md border-2 border-dashed border-orange-500 bg-orange-950/30 px-4 py-3">
          <div className="text-lg font-bold tracking-wide text-orange-300">DRAFT — transaction not terminal</div>
          <div className="text-xs text-orange-200">
            The transaction is <Mono>{data.state}</Mono>. PACT issues hashed receipts only for terminal states; this is the
            unhashed draft returned by the API (refreshing every 3s).
          </div>
        </div>
      )}

      <section className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-4">
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-xs uppercase tracking-widest text-zinc-500">Receipt</span>
          <StateBadge state={finalState} size="lg" />
          <Mono className="text-zinc-500">{p.receipt_version}</Mono>
        </div>
        <p className={`mt-3 text-base font-medium ${stateClasses(finalState).text}`}>{p.outcome_statement}</p>
        <div className="mt-2 text-xs text-zinc-400">{p.objective}</div>
        <div className="mt-2 flex flex-wrap gap-x-6 gap-y-1 text-xs text-zinc-400">
          <span>
            transaction <Mono className="text-zinc-200">{p.transaction_id}</Mono>
          </span>
          <span>initiator {p.initiator}</span>
          <span>created {fmtTime(p.created_at, true)}</span>
          <span>finalized {fmtTime(p.finalized_at, true)}</span>
          <span>{p.event_count} events</span>
        </div>
        {data.kind === "final" && (
          <div className="mt-3 flex flex-wrap items-center gap-3 rounded-md border border-zinc-700 bg-zinc-950/60 p-3">
            <span className="text-xs text-zinc-500">sha256</span>
            <Mono className="text-teal-300">{data.sha256}</Mono>
            <button
              type="button"
              onClick={doVerify}
              disabled={verifying}
              className="ml-auto rounded bg-teal-700 px-3 py-1 text-xs font-semibold text-white hover:bg-teal-600 disabled:opacity-50"
            >
              {verifying ? "Verifying…" : "Verify hash"}
            </button>
            {verify && (
              <div className={`w-full text-xs ${verify.valid ? "text-emerald-300" : "text-red-300"}`}>
                {verify.valid ? "✓ valid" : "✗ INVALID"} — stored <Mono>{verify.stored_sha256.slice(0, 16)}…</Mono> · recomputed{" "}
                <Mono>{verify.recomputed_sha256.slice(0, 16)}…</Mono> · embedded <Mono>{verify.embedded_receipt_hash.slice(0, 16)}…</Mono>
              </div>
            )}
            {verifyErr && <div className="w-full text-xs text-red-300">{verifyErr}</div>}
          </div>
        )}
      </section>

      {uncompensated.length > 0 && (
        <div className="rounded-md border-2 border-red-600 bg-red-950/40 p-4">
          <div className="text-sm font-bold text-red-300">
            Uncompensated effects — {uncompensated.length} effect(s) remain committed or unresolved
          </div>
          <table className="mt-2 w-full text-xs">
            <tbody>
              {uncompensated.map((u) => (
                <tr key={u.operation_key} className="border-b border-red-900/50">
                  <td className="py-1 font-mono text-red-100">{u.effect_type}</td>
                  <td className="py-1">
                    <StateBadge state={u.state} size="xs" />
                  </td>
                  <td className="py-1 font-mono">{u.reversibility_class}</td>
                  <td className="py-1 text-red-200">{u.reason}</td>
                  <td className="py-1 font-mono text-[10px] text-zinc-400">{u.operation_key}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Card title="Children" subtitle="Each child sub-transaction has its own receipt hash, chained into the root receipt">
          <table className="w-full text-xs">
            <tbody>
              {(p.children ?? []).map((c) => (
                <tr key={c.transaction_id} className="border-b border-zinc-900">
                  <td className="py-1 font-mono text-zinc-200">{c.actor_id}</td>
                  <td className="py-1">
                    <StateBadge state={c.final_state} size="xs" />
                  </td>
                  <td className="py-1 font-mono text-[10px] text-teal-300/80">{c.receipt_hash ?? "— (not final)"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        <Card title="Invariant results">
          <table className="w-full text-xs">
            <tbody>
              {(p.invariant_results ?? []).map((r, i) => (
                <tr key={i} className={`border-b border-zinc-900 ${r.passed ? "" : "bg-red-950/30"}`}>
                  <td className="w-5 py-1">
                    <PassMark passed={r.passed} />
                  </td>
                  <td className="py-1">
                    <div className="text-zinc-200">{r.name}</div>
                    <Mono className="text-[10px] text-zinc-500">
                      {r.key} · {r.phase} · {r.context}
                    </Mono>
                  </td>
                  <td className="py-1 text-zinc-400">{r.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {(p.invariant_results ?? []).length === 0 && <div className="text-xs text-zinc-500">none</div>}
        </Card>
      </div>

      <Card title="Effects">
        <table className="w-full text-xs">
          <thead className="text-left text-zinc-500">
            <tr className="border-b border-zinc-800">
              <th className="py-1 font-medium">effect</th>
              <th className="py-1 font-medium">actor</th>
              <th className="py-1 font-medium">final state</th>
              <th className="py-1 font-medium">reversibility</th>
              <th className="py-1 font-medium">amount</th>
              <th className="py-1 font-medium">provider reference</th>
            </tr>
          </thead>
          <tbody>
            {(p.effects ?? []).map((e) => (
              <tr key={e.effect_id} className="border-b border-zinc-900">
                <td className="py-1">
                  <Mono className="text-zinc-200">{e.effect_type}</Mono>
                  <div className="font-mono text-[10px] text-zinc-500">{e.operation_key}</div>
                </td>
                <td className="py-1 font-mono text-zinc-400">{e.actor_id}</td>
                <td className="py-1">
                  <StateBadge state={e.final_state} size="xs" />
                </td>
                <td className="py-1 font-mono">{e.reversibility_class}</td>
                <td className="py-1 font-mono">{e.amount ? money(e.amount) : "—"}</td>
                <td className="py-1 font-mono text-zinc-300">{e.provider_reference ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <Card title="Verification results" subtitle="Provider claim vs. independently verified state, per effect">
        <table className="w-full text-xs">
          <thead className="text-left text-zinc-500">
            <tr className="border-b border-zinc-800">
              <th className="py-1 font-medium">operation</th>
              <th className="py-1 font-medium">provider said</th>
              <th className="py-1 font-medium">reality verified</th>
              <th className="py-1 font-medium">source</th>
              <th className="py-1 font-medium">reference</th>
              <th className="py-1 font-medium">evidence</th>
            </tr>
          </thead>
          <tbody>
            {(p.verification_results ?? []).map((v, i) => (
              <tr key={i} className="border-b border-zinc-900 align-top">
                <td className="py-1 font-mono text-[10px] text-zinc-400">{v.operation_key}</td>
                <td className={`py-1 font-mono ${stateClasses(v.dispatch_outcome).text}`}>{v.dispatch_outcome ?? "—"}</td>
                <td className={`py-1 font-mono ${stateClasses(v.verification_status).text}`}>{v.verification_status}</td>
                <td className={`py-1 font-mono ${v.verification_source === "reconciliation" ? "text-amber-300" : ""}`}>
                  {v.verification_source}
                </td>
                <td className="py-1 font-mono">{v.external_reference ?? "—"}</td>
                <td className="py-1">
                  <Expandable label="evidence">
                    <JsonView value={v.evidence} maxHeight={140} />
                  </Expandable>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {(p.verification_results ?? []).length === 0 && <div className="text-xs text-zinc-500">none</div>}
      </Card>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Card title="Reconciliation events">
          <AttemptTable rows={p.reconciliation_events ?? []} showOutcome />
        </Card>
        <Card title="Compensation results">
          <AttemptTable rows={p.compensation_results ?? []} />
        </Card>
        <Card title="Human actions">
          {(p.human_actions ?? []).length === 0 ? (
            <div className="text-xs text-zinc-500">none</div>
          ) : (
            <JsonView value={p.human_actions} />
          )}
        </Card>
        <Card title="External references">
          <table className="w-full text-xs">
            <tbody>
              {(p.external_references ?? []).map((r, i) => (
                <tr key={i} className="border-b border-zinc-900">
                  <td className="py-1 font-mono text-zinc-400">{r.system}</td>
                  <td className="py-1 font-mono text-zinc-200">{r.reference}</td>
                  <td className="py-1 font-mono text-[10px] text-zinc-500">{r.operation_key}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {(p.external_references ?? []).length === 0 && <div className="text-xs text-zinc-500">none</div>}
        </Card>
      </div>

      <Card
        title="Raw receipt JSON"
        right={
          <button type="button" onClick={() => setRaw((r) => !r)} className="text-xs text-zinc-400 hover:text-zinc-100">
            {raw ? "hide" : "show"}
          </button>
        }
      >
        {raw ? <JsonView value={p} maxHeight={700} /> : <div className="text-xs text-zinc-500">Hidden — click show.</div>}
      </Card>
    </div>
  );
}

function AttemptTable({
  rows,
  showOutcome = false,
}: {
  rows: { operation_key: string; kind: string; attempt_no: number; status: string; outcome?: string; error: string | null; started_at: string | null }[];
  showOutcome?: boolean;
}) {
  if (!rows.length) return <div className="text-xs text-zinc-500">none</div>;
  return (
    <table className="w-full text-xs">
      <tbody>
        {rows.map((r, i) => (
          <tr key={i} className="border-b border-zinc-900 align-top">
            <td className="py-1 font-mono text-[10px] text-zinc-400">{r.operation_key}</td>
            <td className="py-1 font-mono">
              {r.kind} #{r.attempt_no}
            </td>
            <td className={`py-1 font-mono ${stateClasses(r.status).text}`}>{r.status}</td>
            {showOutcome && <td className={`py-1 font-mono ${stateClasses(r.outcome).text}`}>{r.outcome ?? ""}</td>}
            <td className="py-1 font-mono text-red-300">{r.error ?? ""}</td>
            <td className="py-1 font-mono text-zinc-500">{fmtTime(r.started_at)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
