"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { Card, ErrorBox } from "./ui";

type Overview = Awaited<ReturnType<typeof api.operationsOverview>>;

export function OverviewMetrics() {
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const load = () => api.operationsOverview()
      .then((value) => { if (active) { setData(value); setError(null); } })
      .catch((reason: ApiError) => { if (active) setError(reason.message); });
    load();
    const timer = setInterval(load, 5000);
    return () => { active = false; clearInterval(timer); };
  }, []);

  return (
    <Card title="Operational exposure" subtitle="Tenant-wide service counts; refreshed every 5 seconds">
      {error && <ErrorBox title="Overview unavailable" message={error} />}
      {!data && !error && <p className="text-sm text-faint">Loading operational counts…</p>}
      {data && <>
        <div className="mb-4 text-xs text-mute">
          Tenant <strong className="text-ink">{data.tenant_id}</strong> · signed in as{" "}
          <strong className="text-ink">{data.principal}</strong>
        </div>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
          {[
            ["Active", data.active],
            ["Approvals", data.pending_approvals],
            ["Incidents", data.incidents],
            ["Residuals", data.open_residuals],
            ["Worker backlog", data.worker_backlog],
          ].map(([label, value]) => (
            <div key={String(label)} className="neu-inset rounded-xl px-4 py-3">
              <div className="text-xs text-mute">{label}</div>
              <div className="mt-1 text-2xl font-semibold text-ink">{value}</div>
            </div>
          ))}
        </div>
      </>}
    </Card>
  );
}
