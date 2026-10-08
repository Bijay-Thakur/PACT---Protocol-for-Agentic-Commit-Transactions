"use client";

import { useState } from "react";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import { Card, ErrorBox, JsonView } from "./ui";

type Review = Awaited<ReturnType<typeof api.reviewIntent>>;

export function IntentReview() {
  const [intent, setIntent] = useState("");
  const [objective, setObjective] = useState("");
  const [customerId, setCustomerId] = useState("");
  const [reason, setReason] = useState("customer requested cancellation");
  const [clarification, setClarification] = useState("");
  const [requestId, setRequestId] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [draftId, setDraftId] = useState<string | null>(null);
  const [draftStatus, setDraftStatus] = useState<string | null>(null);
  const [resolvedIssueCodes, setResolvedIssueCodes] = useState<string[]>([]);
  const [result, setResult] = useState<{
    proposalTraceId: string; provider: string; live: boolean; model: string | null; review: Review;
    intentIssues: { code: string; detail: string }[];
  } | null>(null);

  async function run(fn: () => Promise<void>) {
    setPending(true);
    setError(null);
    try { await fn(); }
    catch (e) { setError(e instanceof ApiError ? `${e.code}: ${e.message}` : String(e)); }
    finally { setPending(false); }
  }

  function interpret() {
    void run(async () => {
      setResult(null);
      setDraftId(null);
      setDraftStatus(null);
      setResolvedIssueCodes([]);
      const proposal = await api.proposeIntent(intent);
      const review = await api.reviewIntent(proposal.proposal_trace_id, proposal.proposed_plan);
      setObjective(proposal.proposed_plan.objective);
      setCustomerId(String(review.business_request?.customer_id || proposal.proposed_plan.entity_references.customer_id || ""));
      setReason(String(review.business_request?.reason || "customer requested cancellation"));
      setRequestId(crypto.randomUUID());
      setResult({ proposalTraceId: proposal.proposal_trace_id, provider: proposal.provider,
                  live: proposal.live, model: proposal.model, review,
                  intentIssues: proposal.intent_issues });
    });
  }

  function accept() {
    if (!result) return;
    void run(async () => {
      const accepted = await api.acceptIntent({ proposal_trace_id: result.proposalTraceId,
        business_request: { customer_id: customerId, reason },
        clarified_objective: objective, clarification_note: clarification,
        resolved_issue_codes: resolvedIssueCodes }, requestId);
      setDraftId(accepted.transaction_id);
      setDraftStatus("CREATED");
    });
  }

  function assemble() {
    if (!draftId) return;
    void run(async () => {
      const assembled = await api.assembleDraft(draftId);
      setDraftStatus(assembled.status);
      if (assembled.issues?.length) setError(assembled.issues.map((i) => `${i.code}: ${i.detail}`).join("; "));
    });
  }

  function prepare() {
    if (!draftId) return;
    void run(async () => {
      const prepared = await api.prepare(draftId);
      setDraftStatus(prepared.status || prepared.state);
      if (prepared.issues?.length) setError(prepared.issues.map((i) => `${i.code}: ${i.detail}`).join("; "));
    });
  }

  return (
    <Card title="Intent to reviewed transaction"
      subtitle="Interpret, clarify, explicitly accept, assemble from trusted facts, then freeze for a separate approval and exact commit.">
      <div className="flex flex-col gap-3">
        <label htmlFor="pact-intent" className="text-xs font-semibold text-ink">Business intent</label>
        <textarea id="pact-intent" value={intent} onChange={(e) => setIntent(e.target.value)}
          rows={3} maxLength={2000} className="neu-field w-full px-3 py-2 text-sm"
          placeholder="Cancel customer C-123 and refund the unused period" />
        <div><button type="button" className="neu-btn neu-btn-primary px-5 py-2 text-xs"
          disabled={pending || intent.trim().length < 3} onClick={interpret}>Interpret and review</button></div>
        {error && <ErrorBox title="Request needs attention" message={error} />}
        {result && <div className="space-y-3 text-xs text-ink-soft">
          <div>Provider: <strong>{result.provider}</strong> {result.live ? `(live ${result.model})` : "(deterministic fixture)"}</div>
          <div>Review: <strong>{result.review.status}</strong> · workflow: <strong>{result.review.workflow}</strong></div>
          {result.intentIssues.length > 0 && <JsonView value={result.intentIssues} maxHeight={160} />}
          {result.review.issues.length > 0 && <JsonView value={result.review.issues} maxHeight={200} />}
          {result.intentIssues.length > 0 && <fieldset className="space-y-2">
            <legend className="font-semibold text-ink">Issues explicitly resolved by this clarification</legend>
            {result.intentIssues.map((issue) => <label key={issue.code} className="flex gap-2">
              <input type="checkbox" checked={resolvedIssueCodes.includes(issue.code)}
                onChange={(event) => setResolvedIssueCodes((current) => event.target.checked
                  ? [...new Set([...current, issue.code])]
                  : current.filter((code) => code !== issue.code))} />
              <span>{issue.code}: {issue.detail}</span>
            </label>)}
          </fieldset>}
          <label className="block">Clarified objective
            <textarea value={objective} onChange={(e) => setObjective(e.target.value)} rows={2}
              className="neu-field mt-1 w-full px-3 py-2" /></label>
          <label className="block">Customer ID
            <input value={customerId} onChange={(e) => setCustomerId(e.target.value)}
              className="neu-field mt-1 w-full px-3 py-2" /></label>
          <label className="block">Cancellation reason
            <input value={reason} onChange={(e) => setReason(e.target.value)}
              className="neu-field mt-1 w-full px-3 py-2" /></label>
          <label className="block">Clarification note (required if the proposal has unresolved questions)
            <input value={clarification} onChange={(e) => setClarification(e.target.value)}
              className="neu-field mt-1 w-full px-3 py-2" /></label>
          <div>Draft access: {result.review.authorized_to_begin ? "granted" : "sign in as an authorized requester"}</div>
          {!draftId && <button type="button" className="neu-btn neu-btn-primary px-4 py-2"
            disabled={pending || !result.review.authorized_to_begin || !customerId || objective.length < 3}
            onClick={accept}>Accept reviewed request into a draft</button>}
          {draftId && <div className="space-y-2">
            <div>Draft <Link className="underline" href={`/tx/${draftId}`}>{draftId}</Link> · {draftStatus}</div>
            {draftStatus === "CREATED" && <button type="button" className="neu-btn px-4 py-2"
              disabled={pending} onClick={assemble}>Assemble actions from trusted facts</button>}
            {draftStatus === "ASSEMBLED" && <button type="button" className="neu-btn px-4 py-2"
              disabled={pending} onClick={prepare}>Prepare and freeze consequence review</button>}
            {draftStatus === "FROZEN" && <p>The frozen plan is ready for a separate authorized approver. No effect has been sent.</p>}
          </div>}
        </div>}
      </div>
    </Card>
  );
}
