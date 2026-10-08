import Link from "next/link";
import type { PactEvent } from "@/lib/types";

const sample = [
  ["BEGIN", "Root transaction created", "CREATED"],
  ["PROPOSE", "Effect graph and authority registered", "DRAFT"],
  ["PREPARE", "Facts and consequences frozen", "FROZEN"],
  ["ASSESS", "Intent and graph compared", "REVIEW"],
  ["BARRIER", "Global policy checks evaluated", "ELIGIBLE"],
  ["APPROVE", "Operator accepts exact digest", "APPROVED"],
  ["DISPATCH", "Durable intent sent to adapter", "QUEUED"],
  ["OBSERVE", "External state independently checked", "VERIFIED"],
  ["RECEIPT", "Outcome and evidence recorded", "FINAL"],
] as const;

export function IllustrativeKernel() {
  return <div className="kernel" aria-label="Illustrative PACT transaction lifecycle">
    <div className="kernel-header"><span className="kernel-dots" aria-hidden="true"><i /><i /><i /></span>
      <strong>PACT Kernel</strong><span>illustrative transaction</span></div>
    <div className="kernel-list">{sample.map(([label, detail, state], index) =>
      <div className="kernel-row" key={label}><span className="kernel-number">{String(index + 1).padStart(2, "0")}</span>
        <span className="kernel-action">{label}</span><span className="kernel-detail">{detail}</span>
        <span className={`kernel-state ${state === "VERIFIED" || state === "FINAL" ? "kernel-state-good" : ""}`}>{state}</span></div>)}</div>
    <div className="kernel-footer"><span>Provider acceptance alone is never verification.</span>
      <Link href="/demo">Run the real simulator →</Link></div>
  </div>;
}

export function LiveKernel({ id, state, events }: { id: string; state: string; events: PactEvent[] }) {
  return <div className="kernel kernel-live" aria-label="Recorded transaction events">
    <div className="kernel-header"><span className="kernel-dots" aria-hidden="true"><i /><i /><i /></span>
      <strong>PACT Kernel</strong><span>recorded events · {id.slice(0, 8)}</span></div>
    <div className="kernel-list">{events.slice(-12).map((event) => <div className="kernel-row" key={event.sequence}>
      <span className="kernel-number">{String(event.sequence).padStart(2, "0")}</span>
      <span className="kernel-action">{event.event_type}</span>
      <span className="kernel-detail">{event.actor}</span>
      <span className="kernel-state">{new Date(event.created_at).toLocaleTimeString()}</span>
    </div>)}{events.length === 0 && <p className="kernel-empty">No durable events recorded yet.</p>}</div>
    <div className="kernel-footer"><span>Current transaction state: {state}</span><span>{events.length} recorded events</span></div>
  </div>;
}
