import { ScenarioLauncher } from "@/components/ScenarioLauncher";

export default function DemoPage() {
  return (
    <div className="space-y-4">
      <div className="neu-inset rounded-xl border border-warn px-5 py-4 text-sm text-ink">
        <strong>Simulated demo area.</strong> These controls inject fixture faults and are not provider operations.
        The server rejects them unless demo mode is enabled.
      </div>
      <ScenarioLauncher />
    </div>
  );
}
