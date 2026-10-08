"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import type { TransactionSummary } from "@/lib/types";

export function LiveRunPreview() {
  const [rows, setRows] = useState<TransactionSummary[] | null>(null);
  const [message, setMessage] = useState("Checking for an operator session…");
  useEffect(() => {
    let active = true;
    api.me().then(() => api.listTransactions(5)).then((result) => {
      if (active) { setRows(result.transactions); setMessage(""); }
    }).catch(() => { if (active) setMessage("Sign in to the console to view tenant-scoped live runs."); });
    return () => { active = false; };
  }, []);
  return <div className="product-preview"><div className="product-preview-head"><span className="kernel-dots" aria-hidden="true"><i /><i /><i /></span>
    <strong>PACT Console</strong><span>live service data</span></div>
    <div className="product-preview-body"><p className="eyebrow">RECENT TRANSACTIONS</p>
      {rows?.length ? rows.map((row) => <Link key={row.id} href={`/tx/${row.id}`} className="product-run-row">
        <span><strong>{row.objective}</strong><small>{row.id.slice(0, 8)} · {row.actor_id}</small></span>
        <span className="product-run-state">{row.state}</span></Link>) : <p className="product-preview-empty">{rows ? "No transactions for this tenant yet. Run a simulator scenario to create one." : message}</p>}
    </div><div className="product-preview-foot"><Link href="/transactions">Open transaction history →</Link><Link href="/demo">Run a scenario →</Link></div>
  </div>;
}
