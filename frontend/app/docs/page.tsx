import Link from "next/link";
import { CopyBlock } from "@/components/site/CopyBlock";
import { Eyebrow } from "@/components/site/Primitives";

const topics = [
  ["introduction", "Introduction"], ["quickstart", "Quickstart"],
  ["lifecycle", "Transaction lifecycle"], ["semantics", "Semantic review"],
  ["recovery", "Recovery and receipts"], ["interfaces", "REST, MCP, Python"],
  ["configuration", "Configuration"], ["limits", "Current limits"],
] as const;

const setup = [
  "py -3.13 -m venv .venv",
  "& .\\.venv\\Scripts\\python.exe -m pip install -e 'backend[dev]'",
  "Set-Location frontend; npm.cmd ci; Set-Location ..",
  "npm run setup:operator",
  "# Later starts: npm run dev",
].join("\n");

export default function DocsPage() {
  return <div className="docs-layout"><aside className="docs-sidebar"><div className="docs-sidebar-inner">
    <Eyebrow>PACT DOCS</Eyebrow><nav aria-label="Documentation">
      {topics.map(([id, name]) => <a href={`#${id}`} key={id}>{name}</a>)}
    </nav><div className="docs-help"><strong>Need the full contract?</strong><p>The generated OpenAPI snapshot and extension guide live in the repository.</p>
      <a href="https://github.com/Bijay-Thakur/PACT---Protocol-for-Agentic-Commit-Transactions/tree/main/docs/next-phase" target="_blank" rel="noreferrer">View source docs ↗</a></div>
  </div></aside><div className="docs-content">
    <section className="docs-hero site-grid" id="introduction"><Eyebrow>DOCUMENTATION</Eyebrow>
      <h1>Build around the <em>commit boundary.</em></h1>
      <p>PACT is a service for protecting consequential effects in agent workflows. This guide describes the interfaces present in this repository.</p>
      <div className="docs-pill-row"><span>REST /api/v1</span><span>Python convenience client</span><span>MCP stdio</span></div>
    </section>
    <section className="docs-section" id="quickstart"><Eyebrow>01 / QUICKSTART</Eyebrow><h2>Run the local reference service.</h2>
      <p>Prerequisites: Python 3.12+, Node 20+, PostgreSQL 14+, a configured <code>.env</code>, and an empty local development database. Run these commands from the repository root on Windows PowerShell.</p>
      <CopyBlock label="PowerShell · local setup" code={setup} />
      <p>Open <Link href="/console">the console</Link> at port 3000. The API schema is served at <code>http://localhost:8000/docs</code>. The launcher enables simulated demo scenarios locally.</p>
    </section>
    <section className="docs-section" id="lifecycle"><Eyebrow>02 / LIFECYCLE</Eyebrow><h2>From draft to observed outcome.</h2>
      <ol className="docs-steps"><li>Discover a trusted workflow and begin a transaction with scoped authority.</li>
        <li>Delegate or propose typed effects. Review original intent and explicit clarification for natural-language requests.</li>
        <li>Prepare: read trusted facts, check contracts and conflicts, assess semantics, and freeze a digest-bound revision.</li>
        <li>A distinct authorized operator approves the exact digest when required. Commit queues durable dispatch work.</li>
        <li>Observe provider state independently. Reconcile unknowns or record compensation and residual obligations.</li></ol>
    </section>
    <section className="docs-section" id="semantics"><Eyebrow>03 / SEMANTIC REVIEW</Eyebrow><h2>Model advice cannot create authority.</h2>
      <p><code>/api/v1/planner/propose</code> returns a bounded proposal with <code>applied=false</code>. Review uses its protected trace; acceptance requires attributable clarification. A semantic assessment can hold a draft but cannot approve or dispatch an effect. Deterministic policy remains authoritative.</p>
    </section>
    <section className="docs-section" id="recovery"><Eyebrow>04 / RECOVERY</Eyebrow><h2>Unknown is a real state.</h2>
      <p>A lost response can mean the provider applied the effect. PACT retains <code>UNKNOWN</code>, observes by operation identity, and avoids blind duplicate sends. Receipts distinguish a verified business result from an unresolved or partially restored one.</p>
      <Link className="site-inline-link" href="/demo">Inspect the UNKNOWN scenario →</Link>
    </section>
    <section className="docs-section" id="interfaces"><Eyebrow>05 / INTERFACES</Eyebrow><h2>One service, explicit callers.</h2>
      <p>REST exposes the full lifecycle. MCP exposes agent-safe tools through the same service. <code>app.agents.client.HttpPactClient</code> is a Python convenience client in the repository; no standalone SDK has been published.</p>
      <CopyBlock label="PowerShell · authenticated discovery" code={'$headers = @{ Authorization = "Bearer $env:PACT_API_KEY" }\nInvoke-RestMethod -Headers $headers "http://localhost:8000/api/v1/workflows"'} />
      <Link className="site-inline-link" href="/integrate">View integration examples →</Link>
    </section>
    <section className="docs-section" id="configuration"><Eyebrow>06 / CONFIGURATION</Eyebrow><h2>Keep credentials at the boundary.</h2>
      <p>The agent receives a scoped PACT credential. PACT&apos;s adapter owns the protected provider write credential. <code>PACT_DATABASE_URL</code> configures PostgreSQL; <code>PACT_SIM_BASE_URL</code> selects the simulator. Model profiles are optional to the trusted transaction core.</p>
      <p>Groq is the active development model profile. Nebius/Nemotron is wired but live qualification is not established. Do not put provider keys in <code>NEXT_PUBLIC_*</code> variables.</p>
    </section>
    <section className="docs-section" id="limits"><Eyebrow>07 / CURRENT LIMITS</Eyebrow><h2>Reference service, measured scope.</h2>
      <p>The reference Git adapter protects one disposable target when deployed with PACT-only write access. Business providers in the demo are simulated. Human semantic qualification and production provider adoption are outstanding. There is no automatic interception of arbitrary agent tools or guarantee that every effect can be undone.</p>
      <Link className="site-inline-link" href="/get-started">Get the repository and run locally →</Link>
    </section>
  </div></div>;
}
