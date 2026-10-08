import Link from "next/link";
import { Eyebrow, SiteCta } from "@/components/site/Primitives";

const comparison = [
  ["Primary boundary", "One business transaction and its effects", "Recorded activity", "Individual request/response", "Application-defined"],
  ["Cross-step dependencies", "Frozen effect graph with verification gates", "Visible in traces when instrumented", "Outside request scope", "Must be built per workflow"],
  ["Commit decision", "Trusted global barrier before protected dispatch", "Usually outside observability", "Can gate a single request", "Depends on implementation"],
  ["Ambiguous outcome", "UNKNOWN with observation and reconciliation", "Can expose signals", "Often returns an error/timeout", "Depends on implementation"],
  ["Partial recovery", "Contract-bound compensation and residual duties", "Can show what happened", "Outside typical scope", "Depends on implementation"],
] as const;

export default function ComparePage() {
  return <>
    <section className="site-page-hero site-grid"><div className="site-container site-compare-hero">
      <div><Eyebrow>COMPARE</Eyebrow><h1>Agent reliability requires more than <em>a trace.</em></h1>
        <p>Monitoring explains events. PACT sits before protected writes to decide whether one multi-step business transaction may proceed, then records the external result.</p></div>
      <div className="compare-diagram"><span>Agent intent</span><b>→</b><strong>PACT<br /><small>review · barrier · dispatch · observe</small></strong><b>→</b><span>External outcome</span></div>
    </div></section>
    <section className="site-light-section"><div className="site-container">
      <div className="site-section-heading"><div><Eyebrow>CAPABILITY COMPARISON</Eyebrow><h2>Different boundaries solve different problems.</h2></div>
        <p>These are architectural patterns, not claims that every product in a category behaves identically. PACT&apos;s column describes this repository&apos;s reference implementation.</p></div>
      <div className="comparison-scroll"><table className="comparison-table"><thead><tr><th>Dimension</th><th>PACT reference</th><th>Observability</th><th>API / LLM gateway</th><th>Custom orchestration</th></tr></thead>
        <tbody>{comparison.map((row) => <tr key={row[0]}>{row.map((cell, index) => index === 0 ? <th key={index} scope="row">{cell}</th> : <td key={index}>{cell}</td>)}</tr>)}</tbody></table></div>
      <p className="comparison-note">Guarantees depend on the registered adapter, provider behavior, deployment isolation, and available observation. PACT does not automatically control arbitrary tools or promise universal rollback.</p>
      <Link className="site-inline-link site-inline-link-dark" href="/semantic-transactions">Study the transaction model →</Link>
    </div></section>
    <SiteCta title="Inspect the implementation path." text="See a real simulator transaction and the evidence behind its final state." primaryHref="/demo" primary="Run Demo Mode" secondaryHref="/docs" secondary="Read docs" />
  </>;
}
