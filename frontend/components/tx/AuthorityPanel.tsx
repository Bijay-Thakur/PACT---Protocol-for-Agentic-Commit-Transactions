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
      <p className="text-xs text-zinc-400">
        Capability tree: authority issued to the root agent and delegated (attenuated) to child agents. Exposure is the
        amount of proposed effects charged against each capability.
      </p>
      {roots.map((r) => (
        <CapNode key={r.id} cap={r} all={caps} depth={0} />
      ))}
      {caps.length === 0 && <div className="text-sm text-zinc-500">No capabilities.</div>}
    </div>
  );
}

function CapNode({ cap, all, depth }: { cap: Capability; all: Capability[]; depth: number }) {
  const kids = all.filter((c) => c.parent_capability_id === cap.id);
  const ex = cap.exposure;
  const exceeded = ex ? !ex.passed : false;
  return (
    <div className={depth ? "ml-5 border-l border-zinc-700 pl-3" : ""}>
      <div className={`my-1.5 rounded-md border p-3 ${exceeded ? "border-red-700 bg-red-950/20" : "border-zinc-800 bg-zinc-950/40"}`}>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <Mono className="font-semibold text-zinc-100">{cap.subject_id}</Mono>
          <span className="text-zinc-500">issued by</span>
          <Mono className="text-zinc-300">{cap.issuer}</Mono>
          <span className="ml-auto text-zinc-500">
            delegation depth <Mono className="text-zinc-300">{cap.delegation_depth}</Mono>
          </span>
        </div>
        <div className="mt-2 grid grid-cols-1 gap-x-6 gap-y-1 text-[11px] md:grid-cols-2">
          <div>
            <span className="text-zinc-500">effect types </span>
            {cap.allowed_effect_types.map((t) => (
              <Mono key={t} className="mr-1.5 rounded bg-zinc-800 px-1 text-zinc-300">
                {t}
              </Mono>
            ))}
          </div>
          <div>
            <span className="text-zinc-500">resources </span>
            {cap.allowed_resources.map((t) => (
              <Mono key={t} className="mr-1.5 text-zinc-300">
                {t}
              </Mono>
            ))}
          </div>
          <div>
            <span className="text-zinc-500">per-effect limit </span>
            <Mono className="text-zinc-200">{cap.amount_limit !== null ? money(cap.amount_limit) : "—"}</Mono>
            <span className="ml-3 text-zinc-500">cumulative limit </span>
            <Mono className="text-zinc-200">{cap.cumulative_amount_limit !== null ? money(cap.cumulative_amount_limit) : "—"}</Mono>
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
