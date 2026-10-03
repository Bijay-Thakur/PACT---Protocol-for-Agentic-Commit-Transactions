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
      headers: { "content-type": "application/json", ...(init?.headers || {}) },
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
  listTransactions: (limit = 50) =>
    request<{ transactions: TransactionSummary[] }>(`/api/v1/transactions?limit=${limit}`),
  getTransaction: (id: string) => request<TransactionDetail>(`/api/v1/transactions/${id}`),
  getEvents: (id: string, after = 0) =>
    request<{ events: PactEvent[] }>(`/api/v1/transactions/${id}/events?after=${after}&limit=2000`),
  eventStreamUrl: (id: string, after = 0) => `${API_BASE}/api/v1/transactions/${id}/events/stream?after=${after}`,
  getCommitDecision: (id: string) => request<CommitDecision>(`/api/v1/transactions/${id}/commit-decision`),
  getReceipt: (id: string) => request<Receipt>(`/api/v1/transactions/${id}/receipt`),
  verifyReceipt: (id: string) => request<ReceiptVerify>(`/api/v1/transactions/${id}/receipt/verify`),
  prepare: (id: string) => post<{ transaction_id: string; state: string }>(`/api/v1/transactions/${id}/prepare`),
  commit: (id: string, body: { step_delay_ms: number; background: boolean }) =>
    post<{ transaction_id: string; state: string; decision: CommitDecision; explanation: string }>(
      `/api/v1/transactions/${id}/commit`,
      body,
    ),
  reconcile: (id: string) => post<{ transaction_id: string; state: string }>(`/api/v1/transactions/${id}/reconcile`),
  compensate: (id: string) => post<{ transaction_id: string; state: string }>(`/api/v1/transactions/${id}/compensate`),
  operatorAction: (
    id: string,
    body: { operator_id: string; action: OperatorActionType; note: string; effect_id?: string },
  ) => post<{ transaction_id: string; state: string }>(`/api/v1/transactions/${id}/operator-actions`, body),
  listScenarios: () => request<{ scenarios: Scenario[] }>(`/api/v1/demo/scenarios`),
  runScenario: (key: string, body: RunRequest) => post<RunResponse>(`/api/v1/demo/run/${key}`, body),
  externalState: (customerId: string) =>
    request<ExternalState>(`/api/v1/demo/external-state/${encodeURIComponent(customerId)}`),
};

export type DraftReceipt = { state: string; draft: ReceiptPayload };
