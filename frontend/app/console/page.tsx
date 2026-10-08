"use client";

import { TransactionList } from "@/components/TransactionList";
import { OverviewMetrics } from "@/components/OverviewMetrics";

export default function ConsoleOverview() {
  return <div className="space-y-5">
    <div className="console-intro"><div><p className="eyebrow">PACT CONSOLE</p><h1>Transaction overview</h1></div>
      <p>Tenant-scoped work, incidents, and durable outcomes from the running service.</p></div>
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
      <div className="neu-inset px-5 py-4 text-xs leading-relaxed text-mute">
        <span className="font-semibold text-ink">API call result ≠ verified business state.</span> PACT records each attempt and checks provider state. Ambiguous outcomes are reconciled before a justified replay.
      </div>
      <div className="neu-inset px-5 py-4 text-xs leading-relaxed text-mute">
        <span className="font-semibold text-ink">Child validity ≠ global validity.</span> Each agent prepares locally; nothing executes until the root crosses the global commit barrier.
      </div>
    </div>
    <OverviewMetrics /><TransactionList />
  </div>;
}
