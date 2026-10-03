"use client";

import { ScenarioLauncher } from "@/components/ScenarioLauncher";
import { TransactionList } from "@/components/TransactionList";

export default function Home() {
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <div className="rounded-md border border-zinc-800 bg-zinc-900/40 px-4 py-3 text-xs text-zinc-400">
          <span className="font-semibold text-zinc-200">API call result ≠ verified business state.</span> Every effect is
          dispatched once, then independently verified against the provider&apos;s actual state.
        </div>
        <div className="rounded-md border border-zinc-800 bg-zinc-900/40 px-4 py-3 text-xs text-zinc-400">
          <span className="font-semibold text-zinc-200">Child validity ≠ global validity.</span> Each agent prepares
          locally; nothing executes until the root crosses the global commit barrier.
        </div>
      </div>
      <ScenarioLauncher />
      <TransactionList />
    </div>
  );
}
