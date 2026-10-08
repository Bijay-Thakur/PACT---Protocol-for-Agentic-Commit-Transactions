import Link from "next/link";
import { Eyebrow, SectionHeading, SiteCta } from "@/components/site/Primitives";
import { CodeTabs } from "@/components/site/CodeTabs";

export default function IntegratePage() {
  return <>
    <section className="site-page-hero site-grid"><div className="site-container site-page-hero-grid">
      <div><Eyebrow>INTEGRATE WITH PACT</Eyebrow><h1>Keep your agents. Add a <em>commit boundary.</em></h1>
        <p>Your application sends explicit business proposals to PACT. Trusted workflows and adapters decide which effects are protected; agents do not gain provider write authority by wrapping a function.</p>
        <div className="site-actions"><Link className="site-button" href="/docs">Read the quickstart →</Link><Link className="site-button site-button-outline" href="/get-started">Get Started</Link></div>
      </div><CodeTabs />
    </div></section>
    <section className="site-light-section"><div className="site-container">
      <SectionHeading kicker="A REAL INTEGRATION PATH" title={<>One service boundary.<br />Explicit protected effects.</>}
        text="REST is the full lifecycle. MCP exposes agent-safe operations. The repository includes a Python convenience client, but no published SDK package or automatic interception layer." />
      <div className="architecture-line"><div><strong>Existing agent</strong><span>intent and proposed work</span></div><b>→</b>
        <div><strong>PACT REST / MCP</strong><span>authenticated caller</span></div><b>→</b>
        <div><strong>Trusted core</strong><span>semantic review + barrier</span></div><b>→</b>
        <div><strong>PACT adapter</strong><span>protected provider credential</span></div></div>
      <div className="site-feature-grid">
        <article><span className="feature-index">01 /</span><h3>Register a contract</h3><p>A trusted deployment supplies workflow semantics, effect contracts, operation identity, prepare facts, dispatch, observation, and recovery truth.</p></article>
        <article><span className="feature-index">02 /</span><h3>Separate authority</h3><p>The agent holds a scoped PACT credential. A distinct operator can approve the exact frozen digest. Provider write credentials stay beyond the agent.</p></article>
        <article><span className="feature-index">03 /</span><h3>Observe the result</h3><p>Use status, events, and receipts to distinguish queued work, unknown outcomes, verified commits, and residual obligations.</p></article>
      </div>
    </div></section>
    <section className="site-dark-section"><div className="site-container site-two-col"><div>
      <Eyebrow>MODEL PROVIDERS</Eyebrow><h2>Reasoning can vary. The commit barrier does not.</h2></div>
      <div className="site-prose"><p>Groq is the active development profile. Nebius/Nemotron wiring exists, while its live qualification remains open. The transaction engine keeps model assessment advisory and trusted policy authoritative.</p>
        <Link className="site-inline-link" href="/docs#configuration">Configuration details →</Link></div></div></section>
    <SiteCta title="Start with the local service." text="Set up the repository, inspect the versioned API, then connect one explicit protected effect." primaryHref="/get-started" primary="Local setup" secondaryHref="/docs" secondary="Service docs" />
  </>;
}
