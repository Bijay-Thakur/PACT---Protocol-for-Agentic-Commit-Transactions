// Single source of truth for state -> color semantics across the console.

export type Tone = "green" | "slate" | "amber" | "red" | "sky" | "orange" | "indigo" | "neutral";

const TONE_BY_STATE: Record<string, Tone> = {
  COMMITTED_VERIFIED: "green",
  VERIFIED: "green",
  VERIFIED_SUCCESS: "green",
  PASSED: "green",
  ACCEPTED: "green",
  APPLIED: "green",
  FOUND_APPLIED: "green",

  ABORTED: "slate",
  RELEASED: "slate",
  AVAILABLE: "slate",

  UNKNOWN: "amber",
  RECONCILING: "amber",
  VERIFICATION_UNKNOWN: "amber",
  RESPONSE_LOST: "amber",

  FAILED: "red",
  FAILED_TERMINAL: "red",
  VERIFIED_FAILURE: "red",
  REJECTED_DEFINITIVE: "red",
  FOUND_NOT_APPLIED: "red",

  COMPENSATED: "sky",
  COMPENSATING: "sky",

  HUMAN_REQUIRED: "orange",

  COMMITTING: "indigo",
  VERIFYING: "indigo",
  DISPATCHING: "indigo",
  DISPATCHED: "indigo",
  PREPARING: "indigo",
  RESERVED: "indigo",

  PREPARED: "neutral",
  VALIDATED: "neutral",
  PROPOSED: "neutral",
  CREATED: "neutral",
  SPECIFYING: "neutral",
};

export const IN_FLIGHT = new Set(["COMMITTING", "VERIFYING", "DISPATCHING", "DISPATCHED", "PREPARING", "RECONCILING", "COMPENSATING"]);

export const TERMINAL_TX_STATES = new Set(["COMMITTED_VERIFIED", "ABORTED", "COMPENSATED", "FAILED_TERMINAL", "FAILED"]);

export function toneOf(state: string | null | undefined): Tone {
  if (!state) return "neutral";
  const s = state.toUpperCase();
  if (TONE_BY_STATE[s]) return TONE_BY_STATE[s];
  if (s.startsWith("REJECTED")) return "red";
  if (s.startsWith("APPLIED")) return "green";
  if (s.includes("FAIL")) return "red";
  return "neutral";
}

export const TONE_CLASSES: Record<Tone, { badge: string; text: string; border: string; bg: string; dot: string; hex: string }> = {
  green: {
    badge: "text-ok",
    text: "text-ok",
    border: "border-emerald-600",
    bg: "bg-emerald-500/10",
    dot: "bg-emerald-600",
    hex: "#059669",
  },
  slate: {
    badge: "text-steel",
    text: "text-steel",
    border: "border-slate-500",
    bg: "bg-slate-500/10",
    dot: "bg-slate-500",
    hex: "#64748b",
  },
  amber: {
    badge: "text-warn",
    text: "text-warn",
    border: "border-amber-700",
    bg: "bg-amber-500/10",
    dot: "bg-amber-700",
    hex: "#b45309",
  },
  red: {
    badge: "text-bad",
    text: "text-bad",
    border: "border-red-600",
    bg: "bg-red-500/10",
    dot: "bg-red-600",
    hex: "#dc2626",
  },
  sky: {
    badge: "text-info",
    text: "text-info",
    border: "border-sky-600",
    bg: "bg-sky-500/10",
    dot: "bg-sky-600",
    hex: "#0284c7",
  },
  orange: {
    badge: "text-escalate",
    text: "text-escalate",
    border: "border-orange-700",
    bg: "bg-orange-500/10",
    dot: "bg-orange-700",
    hex: "#c2410c",
  },
  indigo: {
    badge: "text-run",
    text: "text-run",
    border: "border-indigo-600",
    bg: "bg-indigo-500/10",
    dot: "bg-indigo-600",
    hex: "#4f46e5",
  },
  neutral: {
    badge: "text-ink-soft",
    text: "text-ink-soft",
    border: "border-slate-500",
    bg: "bg-slate-500/10",
    dot: "bg-slate-500",
    hex: "#6b7790",
  },
};

export function stateClasses(state: string | null | undefined) {
  return TONE_CLASSES[toneOf(state)];
}

export function isInFlight(state: string | null | undefined) {
  return !!state && IN_FLIGHT.has(state.toUpperCase());
}

// Event categories for the timeline.
export type EventCategory =
  | "state"
  | "barrier"
  | "dispatch"
  | "verification"
  | "reconciliation"
  | "compensation"
  | "receipt"
  | "authority"
  | "other";

export function eventCategory(type: string): EventCategory {
  if (type.startsWith("COMPENSATION") || type.includes("COMPENSAT")) return "compensation";
  if (type.startsWith("RECONCILIATION") || type.includes("RECONCIL")) return "reconciliation";
  if (type.includes("VERIF")) return "verification";
  if (type.includes("DISPATCH") || type === "EXECUTION_SCHEDULE_EVALUATED") return "dispatch";
  if (type.includes("BARRIER") || type.startsWith("INVARIANT") || type === "LOGICAL_OPERATIONS_RESERVED") return "barrier";
  if (type.includes("RECEIPT")) return "receipt";
  if (type.includes("CAPABILITY") || type.includes("AUTHORITY")) return "authority";
  if (type.includes("STATE_CHANGED") || type.includes("CREATED") || type.startsWith("EFFECT_") || type.includes("RECOVERY") || type.includes("FINALIZED"))
    return "state";
  return "other";
}

export const CATEGORY_CLASSES: Record<EventCategory, string> = {
  state: "text-ink-soft border-slate-500",
  barrier: "text-barrier border-violet-600",
  dispatch: "text-run border-indigo-600",
  verification: "text-ok border-emerald-600",
  reconciliation: "text-warn border-amber-700",
  compensation: "text-info border-sky-600",
  receipt: "text-evidence border-teal-600",
  authority: "text-authority border-fuchsia-600",
  other: "text-mute border-slate-500",
};

export const HIGHLIGHT_EVENTS = new Set([
  "EFFECT_DISPATCH_UNKNOWN",
  "RECONCILIATION_RESOLVED",
  "COMMIT_BARRIER_EVALUATED",
  "EFFECT_VERIFICATION_FAILED",
]);

export function isHighlightEvent(type: string) {
  return HIGHLIGHT_EVENTS.has(type) || type.startsWith("COMPENSATION_") || type.includes("_COMPENSATION_");
}
