"use client";

import { money, num } from "@/lib/format";

// Categorical colors for per-agent contributions (distinct from state colors).
const SERIES = ["#818cf8", "#2dd4bf", "#f472b6", "#facc15", "#a78bfa", "#60a5fa", "#fb7185"];

export interface Contribution {
  amount: string;
  actor_id: string;
}

/** Stacked horizontal bar of per-agent contributions against a limit line. */
export function ExposureBar({
  contributions,
  exposure,
  limit,
  compact = false,
}: {
  contributions: Contribution[];
  exposure: string | number;
  limit: string | number | null;
  compact?: boolean;
}) {
  const total = num(exposure) || contributions.reduce((s, c) => s + num(c.amount), 0);
  const lim = num(limit);
  const hasLimit = !Number.isNaN(lim);
  const scale = Math.max(total, hasLimit ? lim : 0, 1) * 1.08;
  const over = hasLimit && total > lim;
  const limPct = hasLimit ? (lim / scale) * 100 : null;

  return (
    <div className="w-full">
      <div className={`relative w-full overflow-visible rounded bg-zinc-800 ${compact ? "h-3" : "h-6"}`}>
        <div className="absolute inset-0 flex overflow-hidden rounded">
          {contributions.length > 0 ? (
            contributions.map((c, i) => (
              <div
                key={i}
                title={`${c.actor_id}: ${money(c.amount)}`}
                style={{ width: `${(num(c.amount) / scale) * 100}%`, background: SERIES[i % SERIES.length] }}
                className="h-full border-r-2 border-zinc-900 last:border-r-0"
              />
            ))
          ) : (
            <div
              style={{ width: `${(total / scale) * 100}%` }}
              className={`h-full ${over ? "bg-red-500" : "bg-emerald-500"}`}
            />
          )}
        </div>
        {limPct !== null && (
          <div className="absolute -inset-y-1 w-0.5 bg-zinc-100" style={{ left: `${limPct}%` }} title={`limit ${money(limit)}`}>
            {!compact && (
              <span className="absolute -top-4 left-1 whitespace-nowrap text-[10px] font-semibold text-zinc-200">
                limit {money(limit)}
              </span>
            )}
          </div>
        )}
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px]">
        <span className={over ? "font-semibold text-red-300" : "text-emerald-300"}>
          exposure {money(total)} {hasLimit ? `${over ? ">" : "≤"} limit ${money(lim)}` : "(no limit)"}
        </span>
        {!compact &&
          contributions.map((c, i) => (
            <span key={i} className="flex items-center gap-1 text-zinc-400">
              <span className="inline-block h-2 w-2 rounded-sm" style={{ background: SERIES[i % SERIES.length] }} />
              <span className="font-mono">{c.actor_id}</span> {money(c.amount)}
            </span>
          ))}
      </div>
    </div>
  );
}
