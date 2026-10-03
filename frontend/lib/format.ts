export const shortId = (id: string | null | undefined, n = 8) => (id ? id.slice(0, n) : "—");

export function fmtTime(iso: string | null | undefined, withDate = false) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const t = d.toLocaleTimeString(undefined, { hour12: false }) + "." + String(d.getMilliseconds()).padStart(3, "0");
  return withDate ? `${d.toLocaleDateString()} ${t}` : t;
}

export function fmtRelative(iso: string | null | undefined) {
  if (!iso) return "—";
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return `${Math.max(0, Math.round(diff))}s ago`;
  if (diff < 3600) return `${Math.round(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)}h ago`;
  return new Date(iso).toLocaleString();
}

export const num = (v: string | number | null | undefined) => {
  if (v === null || v === undefined || v === "") return NaN;
  return typeof v === "number" ? v : parseFloat(v);
};

export function money(v: string | number | null | undefined) {
  const n = num(v);
  if (Number.isNaN(n)) return "—";
  return "$" + n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function truncate(s: string | null | undefined, n: number) {
  if (!s) return "";
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

/** Plain-language description of an armed fault (labels the launcher only; state always comes from the API). */
export function describeFault(f: { system: string; operation: string; mode: string; params?: Record<string, unknown> }) {
  const op = `${f.system}.${f.operation}`;
  const msg = f.params && typeof f.params.message === "string" ? ` ("${f.params.message}")` : "";
  switch (`${op}:${f.mode}`) {
    case "billing.create_refund:drop_response_after_apply":
      return "Drop billing response after successful refund";
    case "billing.create_refund:delay_visibility":
      return `Delay refund visibility for ${f.params?.reads ?? "several"} reads (eventual consistency)`;
    case "identity.revoke:reject":
      return `Fail entitlement revoke${msg}`;
    case "subscription.reactivate:reject":
      return "Fail subscription reactivation (compensation fails)";
    case "crm.update:ghost_success":
      return "CRM returns 200 without applying the change";
  }
  const modeText: Record<string, string> = {
    reject: "reject definitively",
    drop_response_after_apply: "apply, then drop the response",
    delay_visibility: "delay visibility",
    ghost_success: "report success without applying",
  };
  return `${op}: ${modeText[f.mode] ?? f.mode}${msg}`;
}

/** Scenario-intrinsic setups that are not provider faults (no entry in `faults`). */
export const SCENARIO_SETUP: Record<string, string> = {
  "invariant-failure": "Propose over-authorized refund ($450 vs $143.27 unused period)",
  "budget-conflict": "Create cross-agent budget conflict (3 locally-valid refunds > shared $10,000)",
  "duplicate-operation": "Re-propose an already-verified refund operation_key",
  unknown: "",
};
