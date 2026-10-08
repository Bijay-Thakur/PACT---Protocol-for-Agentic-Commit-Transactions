"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import { Card, ErrorBox } from "./ui";
import { shortId } from "@/lib/format";

type Approval = Awaited<ReturnType<typeof api.approvalQueue>>["approvals"][number];

export function ApprovalQueue() {
  const [rows, setRows] = useState<Approval[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    api.approvalQueue()
      .then((result) => active && setRows(result.approvals))
      .catch((reason: ApiError) => active && setError(reason.message));
    return () => { active = false; };
  }, []);
  return (
    <Card title="Approvals" subtitle="Only exact current digests authorized for your roles">
      {error && <ErrorBox title="Approval queue unavailable" message={error} />}
      {!rows && !error && <p className="text-sm text-faint">Loading approvals…</p>}
      {rows?.length === 0 && <p className="text-sm text-faint">No plans currently await your approval.</p>}
      <div className="space-y-3">
        {rows?.map((row) => (
          <article key={row.transaction_id} className="neu-inset rounded-xl p-4">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <Link className="font-mono text-run underline" href={`/tx/${row.transaction_id}`}>
                  {shortId(row.transaction_id)}
                </Link>
                <p className="mt-1 text-sm text-ink">{row.objective}</p>
              </div>
              <span className="neu-chip px-2 py-1 text-xs">Revision {row.revision}</span>
            </div>
            <p className="mt-3 break-all font-mono text-[11px] text-mute">Digest {row.digest}</p>
            <p className="mt-2 text-xs text-mute">
              Review projected consequences and semantic issues on the transaction before approving.
            </p>
          </article>
        ))}
      </div>
    </Card>
  );
}
