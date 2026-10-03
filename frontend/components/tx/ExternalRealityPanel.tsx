"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { ExternalState, Json, JsonObject } from "@/lib/types";
import { fmtTime, money } from "@/lib/format";
import { stateClasses } from "@/lib/states";
import { ErrorBox, Expandable, JsonView, Mono } from "../ui";

/** Ground truth inside the simulated providers. Refetches whenever `refreshKey` changes. */
export function ExternalRealityPanel({ customerId, refreshKey }: { customerId: string | undefined; refreshKey: number }) {
  const [data, setData] = useState<ExternalState | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!customerId) return;
    let alive = true;
    api
      .externalState(customerId)
      .then((d) => {
        if (alive) {
          setData(d);
          setError(null);
        }
      })
      .catch((e: ApiError) => alive && setError(`${e.code}: ${e.message}`));
    return () => {
      alive = false;
    };
  }, [customerId, refreshKey]);

  if (!customerId) return <div className="text-sm text-zinc-500">No customer_id in transaction metadata.</div>;
  if (error) return <ErrorBox title="Cannot load external state" message={error} />;
  if (!data) return <div className="text-sm text-zinc-500">Loading provider state…</div>;

  const s = data.state;
  const charges = s.billing?.account?.charges ?? [];
  const refunds = s.billing?.refunds ?? [];
  const sub = s.subscription as JsonObject | null | undefined;
  const iam = s.identity as JsonObject | null | undefined;
  const crm = s.crm as JsonObject | null | undefined;
  const msgs = s.notification?.messages ?? [];

  return (
    <div className="space-y-3">
      <div className="rounded border border-teal-800 bg-teal-950/20 px-3 py-2 text-xs text-teal-200">
        Ground truth inside the simulated providers for customer <Mono>{customerId}</Mono> — read directly from the mock
        systems, not from PACT&apos;s records. Compare it with what PACT claims.
      </div>
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2 xl:grid-cols-3">
        <System name="billing">
          <div className="text-[11px] text-zinc-500">charges</div>
          {charges.map((c) => (
            <div key={c.id} className="font-mono text-[11px] text-zinc-300">
              {c.id} {c.line ? `(${c.line})` : ""} {money(c.amount)} · refunded {money(c.refunded)}
            </div>
          ))}
          <div className="mt-1 text-[11px] text-zinc-500">refunds ({refunds.length})</div>
          {refunds.length === 0 && <div className="text-[11px] text-zinc-500">none</div>}
          {refunds.map((r, i) => (
            <div key={i} className="font-mono text-[11px] text-emerald-300">
              {String(r.id ?? "")} {money(r.amount as string)} {String(r.status ?? "")}
            </div>
          ))}
        </System>
        <System name="subscription">
          {sub ? (
            <>
              <Field k="status" v={sub.status} />
              <Field k="plan" v={sub.plan} />
              <Field k="period" v={`${sub.period_start ?? ""} → ${sub.period_end ?? ""}`} />
              <History h={sub.history} />
            </>
          ) : (
            <None />
          )}
        </System>
        <System name="identity">
          {iam ? (
            <>
              <Field k="entitlements" v={Array.isArray(iam.entitlements) ? (iam.entitlements as Json[]).join(", ") : iam.entitlements} />
              <History h={iam.history} />
            </>
          ) : (
            <None />
          )}
        </System>
        <System name="crm">
          {crm ? (
            <>
              <Field k="lifecycle_state" v={crm.lifecycle_state} />
              <Field k="owner" v={crm.owner} />
              <History h={crm.history} />
            </>
          ) : (
            <None />
          )}
        </System>
        <System name="notification">
          <div className="text-[11px] text-zinc-500">messages ({msgs.length})</div>
          {msgs.map((m, i) => (
            <div key={i} className="font-mono text-[11px] text-zinc-300">
              {String(m.id ?? m.message_id ?? "")} {String(m.template ?? "")} {String(m.status ?? "")}
            </div>
          ))}
        </System>
      </div>
      <div>
        <div className="mb-1 text-[10px] font-semibold uppercase tracking-widest text-zinc-500">
          Provider call log (what actually reached each system)
        </div>
        <table className="w-full text-xs">
          <tbody>
            {data.provider_calls.map((c, i) => (
              <tr key={c.id ?? i} className="border-b border-zinc-900">
                <td className="py-1 font-mono text-zinc-500">{fmtTime(c.at)}</td>
                <td className="py-1 font-mono text-zinc-200">
                  {c.system}.{c.operation}
                </td>
                <td className={`py-1 font-mono ${stateClasses(c.outcome.split(":")[0]).text}`}>→ {c.outcome}</td>
                <td className="py-1 font-mono text-[10px] text-zinc-600">{c.idempotency_key}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {data.provider_calls.length === 0 && <div className="text-xs text-zinc-500">No calls reached any provider.</div>}
      </div>
      <Expandable label="raw external state">
        <JsonView value={data} maxHeight={400} />
      </Expandable>
    </div>
  );
}

function System({ name, children }: { name: string; children: React.ReactNode }) {
  return (
    <div className="rounded-md border border-zinc-800 bg-zinc-950/40 p-3">
      <div className="mb-1 font-mono text-xs font-semibold text-teal-300">{name}</div>
      {children}
    </div>
  );
}

function Field({ k, v }: { k: string; v: Json | undefined }) {
  return (
    <div className="text-[11px]">
      <span className="text-zinc-500">{k}: </span>
      <Mono className="text-zinc-100">{v === undefined || v === null ? "—" : String(v)}</Mono>
    </div>
  );
}

function History({ h }: { h: Json | undefined }) {
  if (!Array.isArray(h) || h.length === 0) return null;
  return (
    <div className="mt-1">
      {h.map((x, i) => {
        const o = x as JsonObject;
        return (
          <div key={i} className="font-mono text-[10px] text-zinc-500">
            {String(o.from ?? "")} → {String(o.to ?? "")} <span className="text-zinc-600">{String(o.key ?? "")}</span>
          </div>
        );
      })}
    </div>
  );
}

const None = () => <div className="text-[11px] text-zinc-500">no record</div>;
