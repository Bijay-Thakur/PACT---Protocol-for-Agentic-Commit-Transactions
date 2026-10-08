import Link from "next/link";
import { Eyebrow } from "@/components/site/Primitives";
import { CopyBlock } from "@/components/site/CopyBlock";

export default function GetStartedPage() {
  return <section className="site-page-hero site-grid"><div className="site-container site-page-hero-grid">
    <div><Eyebrow>GET STARTED</Eyebrow><h1>Put the transaction boundary <em>in front of your agents.</em></h1>
      <p>PACT is currently a repository-run reference service. Start locally, inspect the existing simulator, then register an explicit workflow and adapter for a protected effect.</p>
      <div className="site-actions"><a className="site-button" href="https://github.com/Bijay-Thakur/PACT---Protocol-for-Agentic-Commit-Transactions" target="_blank" rel="noreferrer">View GitHub ↗</a>
        <Link className="site-button site-button-outline" href="/docs">Read setup docs</Link></div>
      <div className="get-started-points"><div><strong>1. Run locally</strong><p>PostgreSQL, Python, and Next.js power the reference deployment.</p></div>
        <div><strong>2. Inspect a demo</strong><p>Real PACT transactions run against isolated simulator state.</p></div>
        <div><strong>3. Define a protected effect</strong><p>Keep provider write credentials beyond the agent boundary.</p></div></div>
    </div><div className="get-started-panel"><h2>Local quickstart</h2><p>From the repository root on Windows PowerShell, after configuring <code>.env</code> and installing dependencies:</p>
      <CopyBlock label="PowerShell" code={"npm run setup:operator\nnpm run dev"} />
      <p>Then open the <Link href="/console">operator console</Link> or <Link href="/demo">Demo Mode</Link>. These routes require a provisioned local operator account.</p>
      <div className="get-started-note">No hosted access form or partnership claim is implied by this reference build.</div>
    </div>
  </div></section>;
}
