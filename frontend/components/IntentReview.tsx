"use client";

import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import { Card, ErrorBox, JsonView } from "./ui";

type Review = Awaited<ReturnType<typeof api.reviewIntent>>;

export function IntentReview() {
  const [intent, setIntent] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{
    provider: string; live: boolean; model: string | null; review: Review;
  } | null>(null);

  async function interpret() {
    setPending(true);
    setError(null);
    setResult(null);
    try {
      const proposal = await api.proposeIntent(intent);
      const review = await api.reviewIntent(proposal.proposed_plan);
      setResult({ provider: proposal.provider, live: proposal.live,
                  model: proposal.model, review });
    } catch (e) {
      setError(e instanceof ApiError ? `${e.code}: ${e.message}` : String(e));
    } finally {
      setPending(false);
    }
  }

  return (
    <Card title="Interpret an intent"
      subtitle="The model extracts a restricted proposal. PACT checks the workflow fields and required action slots before an authorized agent drafts a transaction.">
      <div className="flex flex-col gap-3">
        <label htmlFor="pact-intent" className="text-xs font-semibold text-ink">Business intent</label>
        <textarea id="pact-intent" value={intent} onChange={(e) => setIntent(e.target.value)}
          rows={3} maxLength={2000} className="neu-field w-full px-3 py-2 text-sm"
          placeholder="Cancel customer C-123 and confirm the outcome" />
        <div>
          <button type="button" className="neu-btn neu-btn-primary px-5 py-2 text-xs"
            disabled={pending || intent.trim().length < 3} onClick={interpret}>
            {pending ? "Interpreting…" : "Interpret and review"}
          </button>
        </div>
        {error && <ErrorBox title="Intent review failed" message={error} />}
        {result && (
          <div className="space-y-2 text-xs text-ink-soft">
            <div>Provider: <strong>{result.provider}</strong> {result.live ? `(live ${result.model})` : "(deterministic fixture)"}</div>
            <div>Status: <strong>{result.review.status}</strong> · workflow: <strong>{result.review.workflow}</strong></div>
            <div>Required action slots: {result.review.required_slots.join(", ")}</div>
            <div>Draft access for this login: {result.review.authorized_to_begin ? "granted" : "use an authorized agent"}</div>
            {result.review.business_request && <JsonView value={result.review.business_request} maxHeight={160} />}
            {result.review.issues.length > 0 && <JsonView value={result.review.issues} maxHeight={200} />}
            <p>This review has not created or executed a transaction. An authorized agent must begin, propose effects, prepare, and request commit; required approval is bound to the frozen plan.</p>
          </div>
        )}
      </div>
    </Card>
  );
}
