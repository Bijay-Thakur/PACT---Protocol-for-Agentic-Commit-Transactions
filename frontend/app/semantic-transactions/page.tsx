import Link from "next/link";
import { Eyebrow, SectionHeading, SiteCta } from "@/components/site/Primitives";

const stages = [
  ["01", "Intent", "Original request and explicit clarification"],
  ["02", "Plan", "Trusted workflow and proposed effect graph"],
  ["03", "Prepare", "Facts, conflicts, budgets, and projection"],
  ["04", "Assess", "Semantic concerns and deterministic invariants"],
  ["05", "Approve", "Human authority bound to a frozen digest"],
  ["06", "Dispatch", "Durable operation identity and adapter call"],
  ["07", "Observe", "Independent provider state and reconciliation"],
  ["08", "Receipt", "Verified outcome or explicit residual duty"],
] as const;

export default function SemanticTransactionsPage() {
  return <>
    <section className="site-page-hero site-grid"><div className="site-container site-page-hero-grid">
      <div><Eyebrow>SEMANTIC TRANSACTIONS</Eyebrow><h1>A transaction boundary for <em>agent workflows.</em></h1>
        <p>Agents act across tools and services that share no database transaction. PACT freezes the intended business change, coordinates protected effects, and keeps uncertainty visible until observed.</p>
        <div className="site-actions"><Link className="site-button" href="/demo">See a real demo →</Link><Link className="site-button site-button-outline" href="/docs">Read the lifecycle</Link></div>
      </div>
      <div className="flow-panel"><div className="flow-panel-head"><span>TRANSACTION FLOW</span><span>from intent to evidence</span></div>
        <ol>{stages.map(([number, name, description]) => <li key={name}><span>{number}</span><strong>{name}</strong><small>{description}</small></li>)}</ol>
        <p>After dispatch: reconcile ambiguity, compensate where supported, or retain <code>HUMAN_REQUIRED</code>.</p>
      </div>
    </div></section>
    <section className="site-light-section"><div className="site-container">
      <SectionHeading kicker="THE DISTINCTION" title={<>Consistency beyond<br />one database.</>}
        text="A database can make its own writes atomic. An agent transaction crosses external APIs and human decisions, so its guarantees depend on effect contracts and observable provider state." />
      <div className="site-feature-grid site-feature-grid-four">
        <article><span className="feature-index">01 /</span><h3>Semantic assessment</h3><p>Compare the request and accepted clarification with the compiled consequences. Model output is advisory; unresolved meaning holds dispatch.</p></article>
        <article><span className="feature-index">02 /</span><h3>Global barrier</h3><p>Trusted code checks authority, policy, dependencies, resource conflicts, budgets, and the exact revision before commit.</p></article>
        <article><span className="feature-index">03 /</span><h3>Observation</h3><p>A provider&apos;s HTTP success is not enough. The engine verifies external state and records ambiguity as <code>UNKNOWN</code>.</p></article>
        <article><span className="feature-index">04 /</span><h3>Recovery truth</h3><p>Some effects can be compensated; others cannot. Receipts state what happened and what work remains.</p></article>
      </div>
    </div></section>
    <section className="site-dark-section"><div className="site-container site-two-col">
      <div><Eyebrow>ROLLBACK AND COMPENSATION</Eyebrow><h2>An attempted reversal is not a clean outcome.</h2></div>
      <div className="site-prose"><p>Before dispatch, PACT can block and abort a staged plan. After an effect applies, a contract may offer a restoration action. That action must also be observed.</p>
        <p>If restoration fails or cannot be justified, PACT retains a human obligation. It does not promise global ACID atomicity or automatic reversal of arbitrary APIs.</p>
        <Link className="site-inline-link" href="/faq">Questions about guarantees →</Link></div>
    </div></section>
    <SiteCta title="See the boundary in motion." text="The simulator exposes success, ambiguity, mismatch, and compensation paths through the real transaction service." primaryHref="/demo" primary="Open Demo Mode" secondaryHref="/product" secondary="Explore the console" />
  </>;
}
