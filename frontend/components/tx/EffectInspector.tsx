"use client";

import type { Effect } from "@/lib/types";
import { fmtTime, money, shortId } from "@/lib/format";
import { stateClasses } from "@/lib/states";
import { Expandable, JsonView, KV, Mono, StateBadge } from "../ui";
import { ProviderVsReality } from "./ProviderVsReality";

export function EffectInspector({ effect }: { effect: Effect | null }) {
  if (!effect) return <div className="text-sm text-zinc-500">Select an effect in the DAG.</div>;
  const rec = effect.reconciliation_result;
  const comp = effect.compensation_result;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Mono className="text-sm font-semibold text-zinc-50">{effect.effect_type}</Mono>
        <StateBadge state={effect.state} />
        <span className="text-xs text-zinc-400">
          by <Mono>{effect.actor_id}</Mono>
        </span>
        {effect.amount && <span className="text-xs text-zinc-300">{money(effect.amount)}</span>}
        <Mono className="ml-auto text-[10px] text-zinc-600">{shortId(effect.id)}</Mono>
      </div>

      <ProviderVsReality effect={effect} />

      {rec && (
        <div className="rounded-md border border-amber-800/70 bg-amber-950/20 p-3">
          <div className="text-[10px] font-semibold uppercase tracking-widest text-amber-400">Reconciliation result</div>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs">
            outcome <StateBadge state={rec.outcome ?? null} size="xs" />
            {rec.finding?.finding && (
              <>
                finding <StateBadge state={rec.finding.finding} size="xs" />
              </>
            )}
            {rec.attempt_no !== undefined && <span className="text-zinc-500">attempt {rec.attempt_no}</span>}
          </div>
          {rec.finding?.reason && <div className="mt-1 text-xs text-amber-100">{rec.finding.reason}</div>}
          <Expandable label="raw reconciliation_result">
            <JsonView value={rec} maxHeight={200} />
          </Expandable>
        </div>
      )}

      {comp && (
        <div className="rounded-md border border-sky-800/70 bg-sky-950/20 p-3">
          <div className="text-[10px] font-semibold uppercase tracking-widest text-sky-400">Compensation result</div>
          <div className="mt-1 grid grid-cols-2 gap-3 text-xs">
            <div>
              <div className="text-zinc-500">compensation dispatch said</div>
              {comp.dispatch ? (
                <div className={`font-mono font-semibold ${stateClasses(comp.dispatch.outcome).text}`}>
                  {comp.dispatch.outcome} <span className="text-zinc-400">HTTP {comp.dispatch.http_status ?? "—"}</span>
                </div>
              ) : (
                <div className="text-zinc-500">—</div>
              )}
              {comp.dispatch?.error && <div className="font-mono text-[11px] text-red-300">{comp.dispatch.error}</div>}
            </div>
            <div>
              <div className="text-zinc-500">compensation verified</div>
              {comp.verification ? (
                <>
                  <div className={`font-mono font-semibold ${stateClasses(comp.verification.status).text}`}>
                    {comp.verification.status}
                  </div>
                  <div className="text-[11px] text-zinc-400">{comp.verification.reason}</div>
                </>
              ) : (
                <div className="text-red-300">not verified</div>
              )}
            </div>
          </div>
          <Expandable label="raw compensation_result">
            <JsonView value={comp} maxHeight={200} />
          </Expandable>
        </div>
      )}

      <div>
        <div className="mb-1 text-[10px] font-semibold uppercase tracking-widest text-zinc-500">Attempts</div>
        {effect.attempts.length === 0 ? (
          <div className="text-xs text-zinc-500">No attempts recorded.</div>
        ) : (
          <table className="w-full text-xs">
            <thead className="text-zinc-500">
              <tr className="border-b border-zinc-800 text-left">
                <th className="py-1 font-medium">kind</th>
                <th className="py-1 font-medium">#</th>
                <th className="py-1 font-medium">status</th>
                <th className="py-1 font-medium">http</th>
                <th className="py-1 font-medium">error</th>
                <th className="py-1 font-medium">started</th>
              </tr>
            </thead>
            <tbody>
              {effect.attempts.map((a, i) => (
                <tr key={i} className="border-b border-zinc-900">
                  <td className="py-1 font-mono">{a.kind}</td>
                  <td className="py-1 font-mono">{a.attempt_no}</td>
                  <td className={`py-1 font-mono ${stateClasses(a.status).text}`}>{a.status}</td>
                  <td className="py-1 font-mono">{a.http_status ?? "—"}</td>
                  <td className="py-1 font-mono text-red-300">{a.error ?? ""}</td>
                  <td className="py-1 font-mono text-zinc-400">{fmtTime(a.started_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="rounded-md border border-zinc-800 p-3">
        <KV k="operation_key">
          <Mono>{effect.operation_key}</Mono>
        </KV>
        <KV k="idempotency key">
          <Mono>{effect.provider_idempotency_key ?? "—"}</Mono>
        </KV>
        <KV k="logical operation">
          {effect.logical_operation ? (
            <span className="flex items-center gap-2">
              <StateBadge state={effect.logical_operation.status} size="xs" />
              <Mono className="text-zinc-500">{shortId(effect.logical_operation.id)}</Mono>
            </span>
          ) : (
            "—"
          )}
        </KV>
        <KV k="reversibility">
          <Mono className={effect.reversibility_class === "IRREVERSIBLE" ? "text-red-300" : ""}>{effect.reversibility_class}</Mono>
        </KV>
        <KV k="provider reference">
          <Mono>{effect.provider_reference ?? "—"}</Mono>
        </KV>
        <KV k="depends on">
          {effect.depends_on.length ? (
            effect.depends_on.map((d) => (
              <div key={d}>
                <Mono className="text-zinc-400">{d}</Mono>
              </div>
            ))
          ) : (
            <span className="text-zinc-500">none (level {effect.level})</span>
          )}
        </KV>
        <KV k="resource claims">
          {effect.resource_claims.map((c, i) => (
            <div key={i}>
              <Mono className="text-zinc-400">{c.mode}</Mono> <Mono>{c.resource}</Mono>
            </div>
          ))}
        </KV>
        <KV k="dispatched / verified">
          <Mono>
            {fmtTime(effect.dispatched_at)} / {fmtTime(effect.verified_at)}
          </Mono>
        </KV>
        {effect.contract && (
          <KV k="contract">
            <Mono className="text-zinc-400">
              {effect.contract.adapter_name} · verify={effect.contract.verification_strategy} · compensate=
              {effect.contract.compensation_strategy}
            </Mono>
          </KV>
        )}
        <div className="mt-2 grid grid-cols-1 gap-2 xl:grid-cols-2">
          <div>
            <div className="text-[10px] text-zinc-500">payload</div>
            <JsonView value={effect.payload} maxHeight={160} />
          </div>
          <div>
            <div className="text-[10px] text-zinc-500">prepare_evidence</div>
            <JsonView value={effect.prepare_evidence} maxHeight={160} />
          </div>
        </div>
      </div>
    </div>
  );
}

/** Default selection: the most "interesting" effect (non-verified with external activity), else the first. */
export function pickDefaultEffect(effects: Effect[]): Effect | null {
  if (!effects.length) return null;
  const priority = ["UNKNOWN", "RECONCILING", "HUMAN_REQUIRED", "FAILED", "COMPENSATED", "COMPENSATING", "DISPATCHING", "DISPATCHED", "VERIFYING"];
  for (const s of priority) {
    const e = effects.find((x) => x.state === s);
    if (e) return e;
  }
  const rec = effects.find((e) => e.reconciliation_result);
  if (rec) return rec;
  return effects[0];
}
