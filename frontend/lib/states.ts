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
    badge: "bg-emerald-500/15 text-emerald-300 ring-emerald-500/40",
    text: "text-emerald-300",
    border: "border-emerald-500/60",
    bg: "bg-emerald-500/10",
    dot: "bg-emerald-400",
    hex: "#34d399",
  },
  slate: {
    badge: "bg-slate-500/15 text-slate-300 ring-slate-500/40",
    text: "text-slate-400",
    border: "border-slate-500/60",
    bg: "bg-slate-500/10",
    dot: "bg-slate-400",
    hex: "#94a3b8",
  },
  amber: {
    badge: "bg-amber-500/15 text-amber-300 ring-amber-500/40",
    text: "text-amber-300",
    border: "border-amber-500/70",
    bg: "bg-amber-500/10",
    dot: "bg-amber-400",
    hex: "#fbbf24",
  },
  red: {
    badge: "bg-red-500/15 text-red-300 ring-red-500/40",
    text: "text-red-300",
    border: "border-red-500/70",
    bg: "bg-red-500/10",
    dot: "bg-red-400",
    hex: "#f87171",
  },
  sky: {
    badge: "bg-sky-500/15 text-sky-300 ring-sky-500/40",
    text: "text-sky-300",
    border: "border-sky-500/70",
    bg: "bg-sky-500/10",
    dot: "bg-sky-400",
    hex: "#38bdf8",
  },
  orange: {
    badge: "bg-orange-500/15 text-orange-300 ring-orange-500/40",
    text: "text-orange-300",
    border: "border-orange-500/70",
    bg: "bg-orange-500/10",
    dot: "bg-orange-400",
    hex: "#fb923c",
  },
  indigo: {
    badge: "bg-indigo-500/20 text-indigo-200 ring-indigo-400/50",
    text: "text-indigo-300",
    border: "border-indigo-400/70",
    bg: "bg-indigo-500/10",
    dot: "bg-indigo-400",
    hex: "#818cf8",
  },
  neutral: {
    badge: "bg-zinc-500/15 text-zinc-200 ring-zinc-500/40",
    text: "text-zinc-300",
    border: "border-zinc-600",
    bg: "bg-zinc-500/10",
    dot: "bg-zinc-400",
    hex: "#a1a1aa",
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
  state: "text-zinc-300 border-zinc-600",
  barrier: "text-violet-300 border-violet-500",
  dispatch: "text-indigo-300 border-indigo-500",
  verification: "text-emerald-300 border-emerald-500",
  reconciliation: "text-amber-300 border-amber-500",
  compensation: "text-sky-300 border-sky-500",
  receipt: "text-teal-300 border-teal-500",
  authority: "text-fuchsia-300 border-fuchsia-500",
  other: "text-zinc-400 border-zinc-700",
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
