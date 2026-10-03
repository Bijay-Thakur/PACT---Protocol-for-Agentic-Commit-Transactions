"use client";

import type { DispatchResult, Effect, VerificationResult } from "@/lib/types";
import { stateClasses } from "@/lib/states";
import { Expandable, JsonView, Mono } from "../ui";

/** Commentary on how provider claim and verified reality relate. Derived purely from API fields. */
function relation(d: DispatchResult | null, v: VerificationResult | null, effectState: string) {
  if (!d) return null;
  const said = d.outcome;
  if (!v) {
    if (said === "RESPONSE_LOST" || effectState === "UNKNOWN")
      return { tone: "amber", text: "Provider outcome unknown and reality not yet established — PACT will not blindly retry; it must reconcile by operation identity." };
    if (said === "REJECTED_DEFINITIVE") return { tone: "red", text: "Provider definitively rejected the request; nothing to verify." };
    return { tone: "neutral", text: "Provider responded; independent verification has not produced a result yet." };
  }
  const real = v.status;
  if (said === "ACCEPTED" && real === "VERIFIED_SUCCESS") return { tone: "green", text: "Provider claim confirmed by independent verification." };
  if (said === "ACCEPTED" && real === "VERIFIED_FAILURE")
    return { tone: "red", text: "MISMATCH — the provider said success, but external state shows the change did not happen." };
  if (said === "RESPONSE_LOST" && real === "VERIFIED_SUCCESS")
    return { tone: "amber", text: "The response was lost, but reconciliation found the effect was applied — no duplicate was sent." };
  if (said === "RESPONSE_LOST" && real === "VERIFIED_FAILURE")
    return { tone: "red", text: "The response was lost and reconciliation found the effect was NOT applied." };
  if (real === "VERIFICATION_UNKNOWN") return { tone: "amber", text: "Verification could not establish the outcome." };
  return { tone: "neutral", text: `Provider said ${said}; verification reports ${real}.` };
}

const TONE_BOX: Record<string, string> = {
  green: "border-emerald-600 text-ok",
  red: "border-red-600 text-bad",
  amber: "border-amber-700 text-warn",
  neutral: "border-slate-500 text-ink-soft",
};

export function ProviderVsReality({ effect }: { effect: Effect }) {
  const d = effect.dispatch_result;
  const v = effect.verification_result;
  const rel = relation(d, v, effect.state);
  return (
    <div>
      <div className="grid grid-cols-2 gap-4">
        {/* Provider said */}
        <div className="neu-inset p-4">
          <div className="text-xs font-semibold uppercase tracking-widest text-faint">Provider said</div>
          <div className="text-xs text-faint">dispatch_result — the API call&apos;s response</div>
          {d ? (
            <div className="mt-2 space-y-1">
              <div className={`font-mono text-lg font-bold ${stateClasses(d.outcome).text}`}>{d.outcome}</div>
              <div className="text-xs text-mute">
                HTTP <Mono className="text-ink">{d.http_status ?? "no response"}</Mono>
                {d.latency_ms !== undefined && <span className="ml-2 text-faint">{d.latency_ms} ms</span>}
              </div>
              {d.error && <div className="font-mono text-xs text-bad">{d.error}</div>}
              {d.provider_reference && (
                <div className="text-xs text-mute">
                  ref <Mono>{d.provider_reference}</Mono>
                </div>
              )}
              {d.response !== null && d.response !== undefined && (
                <Expandable label="raw response">
                  <JsonView value={d.response} maxHeight={160} />
                </Expandable>
              )}
            </div>
          ) : (
            <div className="mt-2 text-sm text-faint">
              {effect.state === "ABORTED" ? "Never dispatched — no external call was made." : "Not dispatched yet."}
            </div>
          )}
        </div>
        {/* Reality verified */}
        <div className="neu-inset p-4">
          <div className="text-xs font-semibold uppercase tracking-widest text-faint">Reality verified</div>
          <div className="text-xs text-faint">verification_result — independent query of external state</div>
          {v ? (
            <div className="mt-2 space-y-1">
              <div className={`font-mono text-lg font-bold ${stateClasses(v.status).text}`}>{v.status}</div>
              <div className="flex flex-wrap gap-2 text-xs text-mute">
                <span>
                  source{" "}
                  <Mono className={v.source === "reconciliation" ? "text-warn" : "text-ink"}>{v.source ?? "—"}</Mono>
                </span>
                {v.polls !== undefined && (
                  <span>
                    polls <Mono className="text-ink">{v.polls}</Mono>
                  </span>
                )}
                {v.external_reference && (
                  <span>
                    ref <Mono className="text-ink">{v.external_reference}</Mono>
                  </span>
                )}
              </div>
              {v.reason && <div className="text-xs text-ink-soft">{v.reason}</div>}
              {v.evidence !== undefined && (
                <Expandable label="evidence" defaultOpen>
                  <JsonView value={v.evidence} maxHeight={160} />
                </Expandable>
              )}
            </div>
          ) : (
            <div className="mt-2 text-sm text-faint">
              {!d
                ? "Nothing to verify."
                : effect.state === "UNKNOWN"
                  ? "Unresolved — awaiting reconciliation."
                  : d.outcome === "REJECTED_DEFINITIVE"
                    ? "No verification (definitive rejection)."
                    : "No verification result yet."}
            </div>
          )}
        </div>
      </div>
      {rel && <div className={`neu-inset mt-4 border-l-4 px-4 py-2.5 text-xs ${TONE_BOX[rel.tone]}`}>{rel.text}</div>}
    </div>
  );
}
