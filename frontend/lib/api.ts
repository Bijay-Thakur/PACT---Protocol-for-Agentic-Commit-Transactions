import type {
  CommitDecision,
  ExternalState,
  OperatorActionType,
  PactEvent,
  Receipt,
  ReceiptPayload,
  ReceiptVerify,
  RunRequest,
  RunResponse,
  Scenario,
  TransactionDetail,
  TransactionSummary,
} from "./types";

export const API_BASE = (process.env.NEXT_PUBLIC_PACT_API_URL || "http://localhost:8000").replace(/\/$/, "");

export class ApiError extends Error {
  status: number;
  code: string;
  details: Record<string, unknown> | null;
  constructor(status: number, code: string, message: string, details: Record<string, unknown> | null) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      credentials: "include",
      headers: { "content-type": "application/json",
        ...(init?.method && init.method !== "GET" && typeof window !== "undefined"
          ? { "X-PACT-CSRF": sessionStorage.getItem("pact_csrf") || "" } : {}),
        ...(init?.headers || {}) },
      cache: "no-store",
    });
  } catch (e) {
    throw new ApiError(0, "NETWORK_ERROR", `Cannot reach PACT API at ${API_BASE}: ${(e as Error).message}`, null);
  }
  const text = await res.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  if (!res.ok) {
    const err = (body as { error?: { code?: string; message?: string; details?: Record<string, unknown> } })?.error;
    throw new ApiError(
      res.status,
      err?.code || `HTTP_${res.status}`,
      err?.message || (typeof body === "string" ? body : res.statusText),
      err?.details || null,
    );
  }
  return body as T;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) });

export const api = {
  me: () => request<{ name: string; tenant_id: string; roles: string[] }>("/api/v1/auth/me"),
  login: async (body: { tenant_id: string; username: string; password: string }) => {
    const result = await post<{ csrf_token: string; principal: { name: string; tenant_id: string } }>(
      "/api/v1/auth/login", body);
    sessionStorage.setItem("pact_csrf", result.csrf_token);
    return result;
  },
  logout: async () => {
    await post("/api/v1/auth/logout");
    sessionStorage.removeItem("pact_csrf");
  },
  listTransactions: (limit = 50) =>
    request<{ transactions: TransactionSummary[] }>(`/api/v1/transactions?limit=${limit}`),
  getTransaction: (id: string) => request<TransactionDetail>(`/api/v1/transactions/${id}`),
  getEvents: (id: string, after = 0) =>
    request<{ events: PactEvent[] }>(`/api/v1/transactions/${id}/events?after=${after}&limit=2000`),
  eventStreamUrl: (id: string, after = 0) => `${API_BASE}/api/v1/transactions/${id}/events/stream?after=${after}`,
  getCommitDecision: (id: string) => request<CommitDecision>(`/api/v1/transactions/${id}/commit-decision`),
  getReceipt: (id: string) => request<Receipt>(`/api/v1/transactions/${id}/receipt`),
  verifyReceipt: (id: string) => request<ReceiptVerify>(`/api/v1/transactions/${id}/receipt/verify`),
  prepare: (id: string) => post<{ transaction_id: string; state: string; status?: string;
    digest?: string; issues?: { code: string; detail: string }[] }>(`/api/v1/transactions/${id}/prepare`),
  revise: (id: string, reason: string) =>
    post<{ transaction_id: string; state: string }>(`/api/v1/transactions/${id}/revise`, { reason }),
  commit: (id: string, digest: string) =>
    post<{ transaction_id: string; state: string; status: string; decision: CommitDecision }>(
      `/api/v1/transactions/${id}/commit`,
      { revision_digest: digest },
    ),
  approve: (id: string, digest: string, reason: string) =>
    post<{ transaction_id: string; state: string }>(`/api/v1/transactions/${id}/approve`,
      { revision_digest: digest, reason }),
  reconcile: (id: string, reason: string) =>
    post<{ transaction_id: string; state: string }>(`/api/v1/transactions/${id}/operator-actions`,
      { action: "RECONCILE", reason }),
  compensate: (id: string, reason: string) =>
    post<{ transaction_id: string; state: string }>(`/api/v1/transactions/${id}/operator-actions`,
      { action: "RETRY_RESTORATION", reason }),
  operatorAction: (
    id: string,
    body: { action: OperatorActionType; reason: string; residual_id?: string },
  ) => post<{ transaction_id: string; state: string }>(`/api/v1/transactions/${id}/operator-actions`, body),
  proposeIntent: (intent: string) => post<{
    proposal_trace_id: string;
    intent_issues: { code: string; detail: string }[];
    provider: string; live: boolean; model: string | null; usage: Record<string, number> | null;
    proposed_plan: {
      objective: string; requested_workflow: string; entity_references: Record<string, string>;
      candidate_actions: string[]; requested_parameters: Record<string, string>;
      unresolved_questions: string[];
    };
  }>("/api/v1/planner/propose", { intent }),
  reviewIntent: (proposal: unknown) => post<{
    status: string; workflow: string; business_request: Record<string, unknown> | null;
    required_slots: string[]; issues: { code: string; [key: string]: unknown }[];
    authorized_to_begin: boolean; next_action: string;
  }>("/api/v1/planner/review", { proposal }),
  acceptIntent: (body: { proposal_trace_id: string; business_request: Record<string, unknown>;
    clarified_objective: string; clarification_note: string }, requestId: string) =>
    request<{ transaction_id: string; state: string; next_action: string }>("/api/v1/planner/accept",
      { method: "POST", headers: { "X-PACT-Request-ID": requestId }, body: JSON.stringify(body) }),
  assembleDraft: (id: string) => post<{ status: string; trusted_eligible_refund?: string;
    issues?: { code: string; detail: string }[] }>(`/api/v1/planner/assemble/${id}`),
  listScenarios: () => request<{ scenarios: Scenario[] }>(`/api/v1/demo/scenarios`),
  runScenario: (key: string, body: RunRequest) => post<RunResponse>(`/api/v1/demo/run/${key}`, body),
  externalState: (transactionId: string) =>
    request<ExternalState>(`/api/v1/demo/external-state/${encodeURIComponent(transactionId)}`),
};

export type DraftReceipt = { state: string; draft: ReceiptPayload };
