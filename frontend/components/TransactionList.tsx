"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import type { TransactionSummary } from "@/lib/types";
import { fmtRelative, shortId, truncate } from "@/lib/format";
import { Card, ErrorBox, StateBadge } from "./ui";

export function TransactionList() {
  const [rows, setRows] = useState<TransactionSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () =>
      api
        .listTransactions(100)
        .then((r) => {
          if (!alive) return;
          setRows(r.transactions);
          setError(null);
        })
        .catch((e: ApiError) => alive && setError(e.message));
    load();
    const t = setInterval(load, 3000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  return (
    <Card title="Root transactions" subtitle="Auto-refreshes every 3s" right={rows && <span className="text-xs text-faint">{rows.length} shown</span>}>
      {error && <ErrorBox title="Cannot load transactions" message={error} />}
      {!rows && !error && <div className="text-sm text-faint">Loading…</div>}
      {rows && rows.length === 0 && <div className="text-sm text-faint">No transactions yet — run a scenario above.</div>}
      {rows && rows.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="text-faint">
              <tr className="border-b border-line">
                <th className="py-2 pr-3 font-medium">ID</th>
                <th className="py-2 pr-3 font-medium">Objective</th>
                <th className="py-2 pr-3 font-medium">State</th>
                <th className="py-2 pr-3 font-medium">Scenario</th>
                <th className="py-2 pr-3 text-right font-medium">Children</th>
                <th className="py-2 pr-3 text-right font-medium">Effects</th>
                <th className="py-2 pr-3 font-medium">Invariants</th>
                <th className="py-2 font-medium">Created</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((t) => (
                <tr key={t.id} className="border-b border-line hover:bg-white/50">
                  <td className="py-1.5 pr-3">
                    <Link href={`/tx/${t.id}`} className="font-mono text-run hover:underline">
                      {shortId(t.id)}
                    </Link>
                  </td>
                  <td className="max-w-[420px] py-1.5 pr-3 text-ink-soft" title={t.objective}>
                    <Link href={`/tx/${t.id}`}>{truncate(t.objective, 80)}</Link>
                  </td>
                  <td className="py-1.5 pr-3">
                    <StateBadge state={t.state} />
                  </td>
                  <td className="py-1.5 pr-3 font-mono text-mute">{t.scenario ?? "—"}</td>
                  <td className="py-1.5 pr-3 text-right font-mono">{t.child_count}</td>
                  <td className="py-1.5 pr-3 text-right font-mono">{t.effect_count}</td>
                  <td className="py-1.5 pr-3">{t.invariant_status ? <StateBadge state={t.invariant_status} size="xs" /> : "—"}</td>
                  <td className="py-1.5 text-mute" title={t.created_at}>
                    {fmtRelative(t.created_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
