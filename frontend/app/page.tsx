"use client";

import { ScenarioLauncher } from "@/components/ScenarioLauncher";
import { TransactionList } from "@/components/TransactionList";

export default function Home() {
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <div className="neu-inset px-5 py-4 text-xs leading-relaxed text-mute">
          <span className="font-semibold text-ink">API call result ≠ verified business state.</span> Every effect is
          dispatched once, then independently verified against the provider&apos;s actual state.
        </div>
        <div className="neu-inset px-5 py-4 text-xs leading-relaxed text-mute">
          <span className="font-semibold text-ink">Child validity ≠ global validity.</span> Each agent prepares
          locally; nothing executes until the root crosses the global commit barrier.
        </div>
      </div>
      <ScenarioLauncher />
      <TransactionList />
    </div>
  );
}
