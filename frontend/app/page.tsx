import Link from "next/link";
import { IllustrativeKernel } from "@/components/site/Kernel";
import { Eyebrow, SiteCta, SectionHeading } from "@/components/site/Primitives";

export default function Home() {
  return <>
    <section className="site-hero site-grid"><div className="site-container site-hero-grid">
      <div className="site-hero-copy"><Eyebrow>FOR AGENTIC SYSTEMS</Eyebrow>
        <h1>Semantic transactions for <em>agent systems.</em></h1>
        <p>PACT gives multi-step agent work a protected commit boundary. Review intent and consequences, enforce authority and policy, then observe what external systems actually did.</p>
        <div className="site-actions"><Link className="site-button" href="/semantic-transactions">Explore PACT <span aria-hidden="true">→</span></Link>
          <Link className="site-button site-button-outline" href="/demo">Run a demo</Link></div>
      </div><IllustrativeKernel />
    </div></section>
    <div className="site-capability-strip"><div className="site-container">
      <span>One frozen revision</span><span>Exact-digest approval</span><span>Durable dispatch</span>
      <span>Independent observation</span><span>Honest recovery</span>
    </div></div>
    <section className="site-light-section"><div className="site-container">
      <SectionHeading kicker="WHY PACT" title={<>From agent plans<br />to accountable outcomes.</>}
        text="An agent can make individually valid calls that add up to the wrong business result. PACT coordinates the whole transaction before consequential effects leave the boundary." />
      <div className="site-feature-grid">
        <article><span className="feature-index">01 /</span><h3>Preserve the request</h3><p>Compare the original intent, accepted clarification, and compiled effect graph. Ambiguity becomes a review hold.</p></article>
        <article><span className="feature-index">02 /</span><h3>Commit with authority</h3><p>Trusted policy checks dependencies, delegated grants, resources, budgets, and approvals against one frozen revision.</p></article>
        <article><span className="feature-index">03 /</span><h3>See what happened</h3><p>Provider responses are not proof. PACT observes external state, reconciles unknowns, and records residual duties.</p></article>
      </div>
    </div></section>
    <section className="site-dark-section"><div className="site-container site-two-col">
      <div><Eyebrow>THE EXECUTION BOUNDARY</Eyebrow><h2>Before a tool can change the world, the whole plan gets a decision.</h2></div>
      <div className="site-prose"><p>PACT belongs in the protected write path. Agents may reason and propose independently; a transaction-wide barrier owns consequential dispatch.</p>
        <p>After dispatch, an ambiguous result stays <code>UNKNOWN</code> until independent evidence resolves it. Compensation is attempted only where the effect contract supports it.</p>
        <Link className="site-inline-link" href="/integrate">See the integration boundary →</Link></div>
    </div></section>
    <SiteCta title="Inspect a real transaction path." text="The demo uses the existing simulator and the same service boundary as the operator console." primaryHref="/demo" primary="Open Demo Mode" secondaryHref="/docs" secondary="Read the docs" />
  </>;
}
