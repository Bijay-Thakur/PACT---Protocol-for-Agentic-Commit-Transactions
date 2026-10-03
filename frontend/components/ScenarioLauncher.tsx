"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import type { Scenario } from "@/lib/types";
import { describeFault, SCENARIO_SETUP } from "@/lib/format";
import { Card, ErrorBox, StateBadge } from "./ui";

const PACING = [0, 400, 800];

export function ScenarioLauncher() {
  const router = useRouter();
  const [scenarios, setScenarios] = useState<Scenario[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pacing, setPacing] = useState(400);
  const [pauseAtUnknown, setPauseAtUnknown] = useState(true);
  const [running, setRunning] = useState<string | null>(null);

  useEffect(() => {
    api
      .listScenarios()
      .then((r) => setScenarios(r.scenarios))
      .catch((e: ApiError) => setError(e.message));
  }, []);

  async function run(key: string) {
    setRunning(key);
    setError(null);
    try {
      const res = await api.runScenario(key, {
        background: pacing > 0,
        step_delay_ms: pacing,
        pause_at_unknown: key === "unknown" ? pauseAtUnknown : false,
      });
      router.push(`/tx/${res.root_id}`);
    } catch (e) {
      setError(e instanceof ApiError ? `${e.code}: ${e.message}` : String(e));
      setRunning(null);
    }
  }

  return (
    <Card
      title="Scenario launcher · fault injection"
      subtitle="Each run creates a real root transaction in PACT with delegated child agents. Faults are armed inside the simulated providers."
      right={
        <div className="flex items-center gap-4 text-xs">
          <label className="flex items-center gap-2">
            <span className="text-mute">Live pacing</span>
            <select
              value={pacing}
              onChange={(e) => setPacing(Number(e.target.value))}
              className="neu-field px-3 py-1.5 font-mono"
            >
              {PACING.map((p) => (
                <option key={p} value={p}>
                  {p === 0 ? "0 ms (synchronous)" : `${p} ms / step`}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-2" title="Relevant for scenario D: stop at UNKNOWN so you can trigger reconciliation manually">
            <input type="checkbox" checked={pauseAtUnknown} onChange={(e) => setPauseAtUnknown(e.target.checked)} />
            <span className="text-ink-soft">Pause at UNKNOWN</span>
          </label>
        </div>
      }
    >
      {error && (
        <div className="mb-3">
          <ErrorBox title="Scenario API error" message={error} />
        </div>
      )}
      {!scenarios && !error && <div className="text-sm text-faint">Loading scenarios…</div>}
      <div className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-3">
        {scenarios?.map((s) => {
          const setup = SCENARIO_SETUP[s.key];
          const faults = s.faults.map(describeFault);
          return (
            <div key={s.key} className="neu-raised-md flex flex-col p-5">
              <div className="flex items-start justify-between gap-2">
                <div className="text-sm font-semibold text-ink">{s.title}</div>
                <span className="font-mono text-xs whitespace-nowrap text-faint">{s.key}</span>
              </div>
              <p className="mt-1 text-xs leading-relaxed text-mute">{s.description}</p>
              <div className="mt-2 space-y-1">
                {setup && (
                  <div className="text-xs text-barrier">
                    <span className="text-faint">setup: </span>
                    {setup}
                  </div>
                )}
                {faults.length > 0 ? (
                  faults.map((f, i) => (
                    <div key={i} className="text-xs text-warn">
                      <span className="text-faint">fault: </span>
                      {f}
                    </div>
                  ))
                ) : !setup ? (
                  <div className="text-xs text-faint">no faults armed</div>
                ) : null}
              </div>
              <div className="mt-auto flex items-center justify-between pt-3">
                <div className="flex items-center gap-1.5 text-xs text-faint">
                  expected <StateBadge state={s.expected_state} size="xs" />
                </div>
                <button
                  type="button"
                  disabled={running !== null}
                  onClick={() => run(s.key)}
                  className="neu-btn neu-btn-primary px-5 py-2 text-xs"
                >
                  {running === s.key ? "Running…" : "Run"}
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </Card>
  );
}
