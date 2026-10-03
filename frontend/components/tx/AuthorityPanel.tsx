"use client";

import type { Capability, TransactionDetail } from "@/lib/types";
import { money } from "@/lib/format";
import { Mono } from "../ui";
import { ExposureBar } from "./ExposureBar";

export function AuthorityPanel({ detail }: { detail: TransactionDetail }) {
  const caps = detail.capabilities;
  const ids = new Set(caps.map((c) => c.id));
  const roots = caps.filter((c) => !c.parent_capability_id || !ids.has(c.parent_capability_id));
  return (
    <div className="space-y-2">
      <p className="text-xs text-mute">
        Capability tree: authority issued to the root agent and delegated (attenuated) to child agents. Exposure is the
        amount of proposed effects charged against each capability.
      </p>
      {roots.map((r) => (
        <CapNode key={r.id} cap={r} all={caps} depth={0} />
      ))}
      {caps.length === 0 && <div className="text-sm text-faint">No capabilities.</div>}
    </div>
  );
}

function CapNode({ cap, all, depth }: { cap: Capability; all: Capability[]; depth: number }) {
  const kids = all.filter((c) => c.parent_capability_id === cap.id);
  const ex = cap.exposure;
  const exceeded = ex ? !ex.passed : false;
  return (
    <div className={depth ? "ml-5 border-l-2 border-line pl-3" : ""}>
      <div className={`neu-inset my-2 p-4 ${exceeded ? "border-l-4 border-red-600" : ""}`}>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <Mono className="font-semibold text-ink">{cap.subject_id}</Mono>
          <span className="text-faint">issued by</span>
          <Mono className="text-ink-soft">{cap.issuer}</Mono>
          <span className="ml-auto text-faint">
            delegation depth <Mono className="text-ink-soft">{cap.delegation_depth}</Mono>
          </span>
        </div>
        <div className="mt-2 grid grid-cols-1 gap-x-6 gap-y-1 text-xs md:grid-cols-2">
          <div>
            <span className="text-faint">effect types </span>
            {cap.allowed_effect_types.map((t) => (
              <Mono key={t} className="neu-tag mr-1.5 inline-block px-1.5 py-0.5 text-ink-soft">
                {t}
              </Mono>
            ))}
          </div>
          <div>
            <span className="text-faint">resources </span>
            {cap.allowed_resources.map((t) => (
              <Mono key={t} className="mr-1.5 text-ink-soft">
                {t}
              </Mono>
            ))}
          </div>
          <div>
            <span className="text-faint">per-effect limit </span>
            <Mono className="text-ink">{cap.amount_limit !== null ? money(cap.amount_limit) : "—"}</Mono>
            <span className="ml-3 text-faint">cumulative limit </span>
            <Mono className="text-ink">{cap.cumulative_amount_limit !== null ? money(cap.cumulative_amount_limit) : "—"}</Mono>
          </div>
        </div>
        {ex && (ex.limit !== null || ex.contributions.length > 0) && (
          <div className="mt-2">
            <ExposureBar compact contributions={ex.contributions} exposure={ex.exposure} limit={ex.limit} />
          </div>
        )}
      </div>
      {kids.map((k) => (
        <CapNode key={k.id} cap={k} all={all} depth={depth + 1} />
      ))}
    </div>
  );
}
