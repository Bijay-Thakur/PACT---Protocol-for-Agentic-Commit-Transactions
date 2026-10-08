import Link from "next/link";
import { Eyebrow, SectionHeading, SiteCta } from "@/components/site/Primitives";
import { LiveRunPreview } from "@/components/site/LiveRunPreview";

export default function ProductPage() {
  return <>
    <section className="site-page-hero site-grid"><div className="site-container site-product-grid">
      <div><Eyebrow>PRODUCT</Eyebrow><h1>The control room for <em>consequential work.</em></h1>
        <p>Inspect the plan before approval. Follow every durable attempt and observation afterward. Resolve incidents without pretending that partial recovery is success.</p>
        <div className="site-actions"><Link className="site-button" href="/console">Open Console →</Link><Link className="site-button site-button-outline" href="/demo">Demo Mode</Link></div>
      </div><LiveRunPreview />
    </div></section>
    <section className="site-light-section"><div className="site-container">
      <SectionHeading kicker="OPERATOR WORKFLOWS" title={<>Decisions first.<br />Evidence close behind.</>}
        text="The console uses tenant-scoped service queries. It does not infer global health from a handful of recent rows." />
      <div className="site-feature-grid">
        <article><span className="feature-index">01 /</span><h3>Approval queue</h3><p>Review projected consequences, semantic concerns, policy results, and the exact revision digest before authorizing.</p><Link href="/approvals">Open approvals →</Link></article>
        <article><span className="feature-index">02 /</span><h3>Run inspector</h3><p>Read the effect graph, timeline, external observations, invariants, authority, and receipt for a real transaction.</p><Link href="/transactions">Browse transactions →</Link></article>
        <article><span className="feature-index">03 /</span><h3>Incident queue</h3><p>Find unknown results, mismatches, and residual obligations. Operator actions remain server-authorized.</p><Link href="/incidents">Open incidents →</Link></article>
      </div>
    </div></section>
    <SiteCta title="Start with a measured scenario." text="Run the built-in simulator, then inspect the resulting transaction and receipt." primaryHref="/demo" primary="Run Demo Mode" secondaryHref="/docs" secondary="Read the docs" />
  </>;
}
