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
  const [stateFilter, setStateFilter] = useState("");
  const [search, setSearch] = useState("");
  const [nextCursor, setNextCursor] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () =>
      api
        .listTransactions(100, { state: stateFilter || undefined, search: search || undefined })
        .then((r) => {
          if (!alive) return;
          setRows(r.transactions);
          setNextCursor(r.next_cursor);
          setError(null);
        })
        .catch((e: ApiError) => alive && setError(e.message));
    load();
    const t = setInterval(load, 3000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [stateFilter, search]);

  function loadMore() {
    if (!nextCursor) return;
    api.listTransactions(100, {
      state: stateFilter || undefined,
      search: search || undefined,
      cursor: nextCursor,
    }).then((result) => {
      setRows((current) => [...(current || []), ...result.transactions]);
      setNextCursor(result.next_cursor);
    }).catch((reason: ApiError) => setError(reason.message));
  }

  return (
    <Card title="Root transactions" subtitle="Auto-refreshes every 3s" right={rows && <span className="text-xs text-faint">{rows.length} shown</span>}>
      <div className="mb-4 grid gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(12rem,0.35fr)]">
        <label className="text-xs text-mute">Search objective or business identity
          <input className="neu-field mt-1 w-full px-3 py-2 text-sm" value={search}
            onChange={(event) => setSearch(event.target.value)} placeholder="Customer, operation, or request" />
        </label>
        <label className="text-xs text-mute">State
          <select className="neu-field mt-1 w-full px-3 py-2 text-sm" value={stateFilter}
            onChange={(event) => setStateFilter(event.target.value)}>
            <option value="">All states</option>
            {["CREATED", "SPECIFYING", "PREPARING", "PREPARED", "COMMITTING", "VERIFYING",
              "UNKNOWN", "HUMAN_REQUIRED", "COMMITTED_VERIFIED", "COMPENSATED", "ABORTED"].map(
              (state) => <option key={state} value={state}>{state}</option>,
            )}
          </select>
        </label>
      </div>
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
      {nextCursor && <button type="button" className="neu-btn mt-4 px-4 py-2 text-xs" onClick={loadMore}>
        Load older transactions
      </button>}
    </Card>
  );
}
