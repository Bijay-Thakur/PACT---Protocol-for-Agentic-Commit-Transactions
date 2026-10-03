"use client";

import type { Effect } from "@/lib/types";
import { fmtTime, money, shortId } from "@/lib/format";
import { stateClasses } from "@/lib/states";
import { Expandable, JsonView, KV, Mono, StateBadge } from "../ui";
import { ProviderVsReality } from "./ProviderVsReality";

export function EffectInspector({ effect }: { effect: Effect | null }) {
  if (!effect) return <div className="text-sm text-faint">Select an effect in the DAG.</div>;
  const rec = effect.reconciliation_result;
  const comp = effect.compensation_result;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Mono className="text-sm font-semibold text-ink">{effect.effect_type}</Mono>
        <StateBadge state={effect.state} />
        <span className="text-xs text-mute">
          by <Mono>{effect.actor_id}</Mono>
        </span>
        {effect.amount && <span className="text-xs text-ink-soft">{money(effect.amount)}</span>}
        <Mono className="ml-auto text-xs text-faint">{shortId(effect.id)}</Mono>
      </div>

      <ProviderVsReality effect={effect} />

      {rec && (
        <div className="neu-inset border-l-4 border-amber-700 p-4">
          <div className="text-xs font-semibold uppercase tracking-widest text-warn">Reconciliation result</div>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs">
            outcome <StateBadge state={rec.outcome ?? null} size="xs" />
            {rec.finding?.finding && (
              <>
                finding <StateBadge state={rec.finding.finding} size="xs" />
              </>
            )}
            {rec.attempt_no !== undefined && <span className="text-faint">attempt {rec.attempt_no}</span>}
          </div>
          {rec.finding?.reason && <div className="mt-1 text-xs text-warn">{rec.finding.reason}</div>}
          <Expandable label="raw reconciliation_result">
            <JsonView value={rec} maxHeight={200} />
          </Expandable>
        </div>
      )}

      {comp && (
        <div className="neu-inset border-l-4 border-sky-600 p-4">
          <div className="text-xs font-semibold uppercase tracking-widest text-info">Compensation result</div>
          <div className="mt-1 grid grid-cols-2 gap-3 text-xs">
            <div>
              <div className="text-faint">compensation dispatch said</div>
              {comp.dispatch ? (
                <div className={`font-mono font-semibold ${stateClasses(comp.dispatch.outcome).text}`}>
                  {comp.dispatch.outcome} <span className="text-mute">HTTP {comp.dispatch.http_status ?? "—"}</span>
                </div>
              ) : (
                <div className="text-faint">—</div>
              )}
              {comp.dispatch?.error && <div className="font-mono text-xs text-bad">{comp.dispatch.error}</div>}
            </div>
            <div>
              <div className="text-faint">compensation verified</div>
              {comp.verification ? (
                <>
                  <div className={`font-mono font-semibold ${stateClasses(comp.verification.status).text}`}>
                    {comp.verification.status}
                  </div>
                  <div className="text-xs text-mute">{comp.verification.reason}</div>
                </>
              ) : (
                <div className="text-bad">not verified</div>
              )}
            </div>
          </div>
          <Expandable label="raw compensation_result">
            <JsonView value={comp} maxHeight={200} />
          </Expandable>
        </div>
      )}

      <div>
        <div className="mb-1 text-xs font-semibold uppercase tracking-widest text-faint">Attempts</div>
        {effect.attempts.length === 0 ? (
          <div className="text-xs text-faint">No attempts recorded.</div>
        ) : (
          <table className="w-full text-xs">
            <thead className="text-faint">
              <tr className="border-b border-line text-left">
                <th className="py-1.5 pr-4 font-medium">kind</th>
                <th className="py-1.5 pr-4 font-medium">#</th>
                <th className="py-1.5 pr-4 font-medium">status</th>
                <th className="py-1.5 pr-4 font-medium">http</th>
                <th className="py-1.5 pr-4 font-medium">error</th>
                <th className="py-1.5 pr-4 font-medium">started</th>
              </tr>
            </thead>
            <tbody>
              {effect.attempts.map((a, i) => (
                <tr key={i} className="border-b border-line">
                  <td className="py-1.5 pr-4 font-mono">{a.kind}</td>
                  <td className="py-1.5 pr-4 font-mono">{a.attempt_no}</td>
                  <td className={`py-1.5 pr-4 font-mono ${stateClasses(a.status).text}`}>{a.status}</td>
                  <td className="py-1.5 pr-4 font-mono">{a.http_status ?? "—"}</td>
                  <td className="py-1.5 pr-4 font-mono text-bad">{a.error ?? ""}</td>
                  <td className="py-1.5 pr-4 font-mono text-mute">{fmtTime(a.started_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="neu-raised-md p-4">
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
              <Mono className="text-faint">{shortId(effect.logical_operation.id)}</Mono>
            </span>
          ) : (
            "—"
          )}
        </KV>
        <KV k="reversibility">
          <Mono className={effect.reversibility_class === "IRREVERSIBLE" ? "text-bad" : ""}>{effect.reversibility_class}</Mono>
        </KV>
        <KV k="provider reference">
          <Mono>{effect.provider_reference ?? "—"}</Mono>
        </KV>
        <KV k="depends on">
          {effect.depends_on.length ? (
            effect.depends_on.map((d) => (
              <div key={d}>
                <Mono className="text-mute">{d}</Mono>
              </div>
            ))
          ) : (
            <span className="text-faint">none (level {effect.level})</span>
          )}
        </KV>
        <KV k="resource claims">
          {effect.resource_claims.map((c, i) => (
            <div key={i}>
              <Mono className="text-mute">{c.mode}</Mono> <Mono>{c.resource}</Mono>
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
            <Mono className="text-mute">
              {effect.contract.adapter_name} · verify={effect.contract.verification_strategy} · compensate=
              {effect.contract.compensation_strategy}
            </Mono>
          </KV>
        )}
        <div className="mt-2 grid grid-cols-1 gap-2 xl:grid-cols-2">
          <div>
            <div className="text-xs text-faint">payload</div>
            <JsonView value={effect.payload} maxHeight={160} />
          </div>
          <div>
            <div className="text-xs text-faint">prepare_evidence</div>
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
