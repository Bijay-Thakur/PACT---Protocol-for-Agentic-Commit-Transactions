"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import { Card, ErrorBox, StateBadge } from "./ui";
import { fmtRelative, shortId } from "@/lib/format";

type Incident = Awaited<ReturnType<typeof api.incidentQueue>>["incidents"][number];

export function IncidentQueue() {
  const [rows, setRows] = useState<Incident[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    api.incidentQueue()
      .then((result) => active && setRows(result.incidents))
      .catch((reason: ApiError) => active && setError(reason.message));
    return () => { active = false; };
  }, []);
  return (
    <Card title="Incidents" subtitle="UNKNOWN, mismatch, and residual obligations requiring action">
      {error && <ErrorBox title="Incident queue unavailable" message={error} />}
      {!rows && !error && <p className="text-sm text-faint">Loading incidents…</p>}
      {rows?.length === 0 && <p className="text-sm text-faint">No unresolved incidents are visible.</p>}
      <div className="space-y-3">
        {rows?.map((row) => (
          <article key={row.transaction_id} className="neu-inset rounded-xl p-4">
            <div className="flex flex-wrap items-center gap-2">
              <StateBadge state={row.state} />
              <Link className="font-mono text-run underline" href={`/tx/${row.transaction_id}`}>
                {shortId(row.transaction_id)}
              </Link>
              <span className="text-xs text-mute">updated {fmtRelative(row.updated_at)}</span>
            </div>
            <p className="mt-2 text-sm text-ink">{row.objective}</p>
            {row.residuals.map((residual) => (
              <div key={residual.id} className="mt-3 border-l-2 border-warn pl-3 text-xs text-mute">
                <strong className="text-ink">{residual.kind}</strong>: {residual.description}
                <div>Next: {residual.required_remediation}</div>
              </div>
            ))}
            <p className="mt-3 text-xs text-mute">Server-permitted actions: {row.permitted_actions.join(", ")}</p>
          </article>
        ))}
      </div>
    </Card>
  );
}
