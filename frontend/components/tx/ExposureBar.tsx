"use client";

import { money, num } from "@/lib/format";

// Categorical colors for per-agent contributions (distinct from state colors).
// All >= 3:1 against the surface so bars stay distinguishable (checked with WCAG contrast).
const SERIES = ["#4f46e5", "#0f766e", "#be185d", "#a16207", "#6d28d9", "#1d4ed8", "#be123c"];

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
      <div className={`neu-inset-sm relative w-full overflow-visible ${compact ? "h-3" : "h-6"}`}>
        <div className="absolute inset-0 flex overflow-hidden rounded-[10px]">
          {contributions.length > 0 ? (
            contributions.map((c, i) => (
              <div
                key={i}
                title={`${c.actor_id}: ${money(c.amount)}`}
                style={{ width: `${(num(c.amount) / scale) * 100}%`, background: SERIES[i % SERIES.length] }}
                className="h-full border-r-2 border-neu last:border-r-0"
              />
            ))
          ) : (
            <div
              style={{ width: `${(total / scale) * 100}%` }}
              className={`h-full ${over ? "bg-red-600" : "bg-emerald-600"}`}
            />
          )}
        </div>
        {limPct !== null && (
          <div className="absolute -inset-y-1 w-0.5" style={{ left: `${limPct}%` }} title={`limit ${money(limit)}`}>
            <span aria-hidden className="absolute inset-0 rounded bg-ink" />
            {!compact && (
              <span className="absolute -top-4 left-1 whitespace-nowrap text-xs font-semibold text-ink">
                limit {money(limit)}
              </span>
            )}
          </div>
        )}
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
        <span className={over ? "font-semibold text-bad" : "text-ok"}>
          exposure {money(total)} {hasLimit ? `${over ? ">" : "≤"} limit ${money(lim)}` : "(no limit)"}
        </span>
        {!compact &&
          contributions.map((c, i) => (
            <span key={i} className="flex items-center gap-1 text-mute">
              <span className="inline-block h-2 w-2 rounded-sm" style={{ background: SERIES[i % SERIES.length] }} />
              <span className="font-mono">{c.actor_id}</span> {money(c.amount)}
            </span>
          ))}
      </div>
    </div>
  );
}
