// Types mirroring the PACT backend read models (see /api/v1 responses).
// Monetary amounts are serialized as decimal strings by the backend.

export type Json = string | number | boolean | null | Json[] | { [k: string]: Json };
export type JsonObject = { [k: string]: Json };

export interface ApiErrorBody {
  error: { code: string; message: string; details?: JsonObject | null };
}

// ---------- list ----------
export interface TransactionSummary {
  id: string;
  objective: string;
  state: string;
  actor_id: string;
  child_count: number;
  effect_count: number;
  invariant_status: string | null;
  scenario: string | null;
  customer_id: string | null;
  created_at: string;
  updated_at: string;
  finalized_at: string | null;
}

// ---------- demo ----------
export interface ScenarioFault {
  system: string;
  operation: string;
  mode: string;
  params?: JsonObject;
}

export interface Scenario {
  key: string;
  title: string;
  description: string;
  expected_state: string;
  faults: ScenarioFault[];
}

export interface RunRequest {
  background: boolean;
  step_delay_ms: number;
  pause_at_unknown: boolean;
}

export interface RunResponse {
  scenario: string;
  title: string;
  customer_id: string;
  root_id: string;
  expected_state: string;
  faults: ScenarioFault[];
  commit_decision: { eligible: boolean; blocking_reasons: string[] } | null;
  state: string;
  provider_calls?: string[];
  matches_expected?: boolean;
}

// ---------- detail ----------
export interface Transaction {
  id: string;
  root_id: string;
  parent_id: string | null;
  depth: number;
  objective: string;
  actor_id: string;
  state: string;
  required: boolean;
  capability_id: string | null;
  version: number;
  effect_count: number;
  child_count: number;
  created_at: string;
  updated_at: string;
  finalized_at: string | null;
  receipt_hash: string | null;
}

export interface Policy {
  on_unknown?: string;
  auto_reconcile?: boolean;
  on_effect_failure?: string;
  on_compensation_failure?: string;
  max_auto_reconcile_rounds?: number;
  [k: string]: Json | undefined;
}

export interface TxMetadata {
  scenario?: string;
  customer_id?: string;
  participants?: string[];
  business_date?: string;
  requires_approval?: boolean;
  required_effect_types?: string[];
  [k: string]: Json | undefined;
}

export interface ExposureContribution {
  amount: string;
  actor_id: string;
  transaction_id?: string;
  operation_key?: string;
}

export interface Exposure {
  exposure: string;
  limit: string | null;
  passed: boolean;
  contributions: ExposureContribution[];
}

export interface Capability {
  id: string;
  transaction_id: string;
  subject_id: string;
  issuer: string;
  parent_capability_id: string | null;
  allowed_effect_types: string[];
  allowed_resources: string[];
  amount_limit: string | null;
  cumulative_amount_limit: string | null;
  delegation_depth: number;
  expires_at: string | null;
  exposure: Exposure | null;
}

export interface DispatchResult {
  error: string | null;
  outcome: string;
  response: Json;
  latency_ms?: number;
  http_status: number | null;
  provider_reference: string | null;
}

export interface VerificationResult {
  polls?: number;
  reason?: string;
  source?: string;
  status: string;
  evidence?: Json;
  external_reference?: string | null;
}

export interface ReconciliationResult {
  finding?: {
    reason?: string;
    finding?: string;
    evidence?: Json;
    external_reference?: string | null;
  } | null;
  outcome?: string;
  attempt_no?: number;
  verification?: VerificationResult | null;
}

export interface CompensationResult {
  dispatch?: DispatchResult | null;
  attempt_no?: number;
  verification?: VerificationResult | null;
}

export interface Attempt {
  kind: string;
  attempt_no: number;
  status: string;
  http_status: number | null;
  error: string | null;
  started_at: string | null;
  finished_at?: string | null;
}

export interface ResourceClaim {
  mode: string;
  resource: string;
  operation_key?: string;
}

export interface EffectContract {
  effect_type: string;
  adapter_name?: string;
  resource_type?: string;
  operation_kind?: string;
  reversibility_class?: string;
  verification_strategy?: string;
  compensation_strategy?: string;
  idempotency_strategy?: string;
  reconciliation_policy?: string;
  description?: string;
  [k: string]: Json | undefined;
}

export interface Effect {
  id: string;
  transaction_id: string;
  actor_id: string;
  effect_type: string;
  operation_key: string;
  state: string;
  payload: JsonObject;
  amount: string | null;
  resource_claims: ResourceClaim[];
  depends_on: string[];
  reversibility_class: string;
  provider_idempotency_key: string | null;
  provider_reference: string | null;
  prepare_evidence: Json;
  dispatch_result: DispatchResult | null;
  verification_result: VerificationResult | null;
  reconciliation_result: ReconciliationResult | null;
  compensation_result: CompensationResult | null;
  dispatched_at: string | null;
  verified_at: string | null;
  level: number;
  logical_operation: { id: string; status: string; owner_root_id?: string | null } | null;
  attempts: Attempt[];
  contract: EffectContract | null;
}

export interface TreeNode {
  id: string;
  root_id: string;
  parent_id: string | null;
  depth: number;
  objective: string;
  actor_id: string;
  state: string;
  required: boolean;
  capability_id: string | null;
  effect_count: number;
  child_count: number;
  receipt_hash: string | null;
  created_at: string;
  updated_at: string;
  finalized_at: string | null;
}

export interface Graph {
  valid: boolean;
  cycles: Json[];
  unresolved?: JsonObject;
  edges: { from: string; to: string }[];
}

export interface InvariantEvaluation {
  context: string;
  phase: string;
  passed: boolean;
  reason: string;
  observed_values: Json;
  at: string;
}

export interface Invariant {
  id: string;
  transaction_id: string;
  key: string;
  name: string;
  phase: string;
  expression_type: string;
  config: JsonObject;
  failure_action: string;
  severity?: string;
  evaluations: InvariantEvaluation[];
}

export interface BarrierCheck {
  code: string;
  passed: boolean;
  subject: string | null;
  detail?: string;
  observed?: Json;
  blocking_reason?: string | null;
}

export interface CommitDecision {
  id?: string;
  transaction_id?: string;
  eligible: boolean;
  binding?: boolean;
  snapshot_version?: number;
  checks: BarrierCheck[];
  blocking_reasons: string[];
  evaluated_at?: string;
  explanation?: string;
}

export interface OperatorAction {
  id?: string;
  operator_id: string;
  action: string;
  note?: string;
  effect_id?: string | null;
  created_at?: string;
  [k: string]: Json | undefined;
}

export interface TransactionDetail {
  transaction: Transaction;
  root_id: string;
  policy: Policy;
  plan_revision?: { number: number; status: string; digest: string | null;
    approval_required: boolean; approval?: { role?: string; reason?: string };
    projection?: Record<string, unknown>; required_outcomes?: Record<string, unknown>[];
    candidate_digest?: string | null; semantic_disposition?: string | null;
    semantic_assessment?: Record<string, unknown> | null;
    issues?: Record<string, unknown>[] } | null;
  metadata: TxMetadata;
  tree: TreeNode[];
  capabilities: Capability[];
  effects: Effect[];
  graph: Graph;
  invariants: Invariant[];
  commit_decisions: CommitDecision[];
  dry_run_decision: CommitDecision | null;
  operator_actions: OperatorAction[];
  receipts: { transaction_id: string; sha256: string }[];
  event_count: number;
}

// ---------- events ----------
export interface PactEvent {
  sequence: number;
  id?: string;
  transaction_id: string;
  effect_id: string | null;
  event_type: string;
  actor: string;
  payload: JsonObject;
  created_at: string;
}

// ---------- external state ----------
export interface ProviderCall {
  id?: number;
  system: string;
  operation: string;
  outcome: string;
  idempotency_key: string | null;
  at: string;
}

export interface ExternalState {
  state: {
    customer_id?: string;
    billing?: {
      account?: {
        charges?: { id: string; line?: string; amount: string; refunded: string }[];
        currency?: string;
        customer_id?: string;
      } | null;
      refunds?: JsonObject[];
    };
    subscription?: JsonObject | null;
    identity?: JsonObject | null;
    crm?: JsonObject | null;
    notification?: { messages?: JsonObject[] };
  };
  provider_calls: ProviderCall[];
}

// ---------- receipts ----------
export interface ReceiptPayload {
  receipt_version?: string;
  transaction_id: string;
  root_id?: string;
  parent_id?: string | null;
  objective?: string;
  initiator?: string | { name: string; principal_id: string | null; kind: string | null };
  participants?: string[];
  created_at?: string;
  finalized_at?: string | null;
  final_state: string;
  outcome_statement?: string;
  capability_summary?: JsonObject[];
  children?: { actor_id: string; final_state: string; receipt_hash: string | null; transaction_id: string }[];
  effects?: {
    effect_id: string;
    effect_type: string;
    actor_id: string;
    operation_key?: string;
    operation_identity?: string;
    final_state: string;
    reversibility_class: string;
    provider_reference: string | null;
    amount: string | null;
    requested_amount?: string | null;
    transaction_id?: string;
    depends_on?: string[];
    payload?: JsonObject;
  }[];
  invariant_results?: {
    key: string;
    name: string;
    phase: string;
    passed: boolean;
    reason: string;
    context: string;
    failure_action?: string;
    observed?: Json;
  }[];
  commit_decision?: CommitDecision | null;
  resource_claims?: ResourceClaim[];
  execution_results?: (Attempt & { operation_key: string })[];
  verification_results?: {
    operation_key: string;
    dispatch_outcome: string | null;
    verification_status: string;
    verification_source: string | null;
    evidence: Json;
    external_reference: string | null;
    verified_at: string | null;
  }[];
  reconciliation_events?: (Attempt & { operation_key: string; outcome?: string })[];
  compensation_results?: (Attempt & { operation_key: string })[];
  uncompensated_effects?: {
    operation_key: string;
    effect_type: string;
    state: string;
    reversibility_class: string;
    reason: string;
  }[];
  final_observation_set?: {
    observation_id: string; operation_identity: string; purpose: string;
    source: string; application: string; postcondition: string;
    observed_amount: string | null; provider_reference: string | null;
    evidence_digest: string; at: string;
  }[];
  residual_obligations?: {
    id: string; operation_identity: string; kind: string; description: string;
    amount: string | null; currency: string | null; blocking: boolean;
    disposition: string; required_remediation: string | null;
  }[];
  human_actions?: JsonObject[];
  external_references?: { system?: string; provider?: string; reference: string;
    operation_key?: string; operation_identity?: string }[];
  event_count?: number;
  receipt_hash?: string;
}

export interface Receipt {
  transaction_id: string;
  sha256: string;
  receipt_version: string;
  final_state: string;
  created_at: string;
  payload: ReceiptPayload;
}

export interface ReceiptVerify {
  transaction_id?: string;
  valid: boolean;
  stored_sha256: string;
  recomputed_sha256: string;
  embedded_receipt_hash: string;
}

export type OperatorActionType =
  | "FINALIZE_FAILED"
  | "RECONCILE"
  | "RETRY_RESTORATION"
  | "ATTEST_RESIDUAL";
