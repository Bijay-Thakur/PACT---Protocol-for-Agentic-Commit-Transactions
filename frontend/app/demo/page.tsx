import { ScenarioLauncher } from "@/components/ScenarioLauncher";
import Link from "next/link";

export default function DemoPage() {
  return (
    <div className="space-y-4">
      <div className="console-intro"><div><p className="eyebrow">SIMULATED REFERENCE</p><h1>Demo Mode</h1></div>
        <Link href="/" className="text-sm text-accent hover:underline">Return to product site →</Link></div>
      <div className="neu-inset rounded-xl border border-warn px-5 py-4 text-sm text-ink">
        <strong>Simulated demo area.</strong> These controls inject fixture faults and are not provider operations.
        The server rejects them unless demo mode is enabled.
      </div>
      <ScenarioLauncher />
    </div>
  );
}
