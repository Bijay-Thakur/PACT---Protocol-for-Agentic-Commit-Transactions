import Link from "next/link";
import { Eyebrow } from "@/components/site/Primitives";

const questions = [
  ["What is a semantic transaction?", "A transaction-wide plan for consequential agent effects. PACT compares intent and expected consequences, checks trusted policy, coordinates protected dispatch, then observes external outcomes."],
  ["How is this different from a database transaction?", "A database transaction protects operations inside its own scope. PACT coordinates effects across separate tools and services, where global ACID atomicity is not available."],
  ["How is PACT different from observability?", "Traces tell you what happened. PACT also checks a frozen plan before protected writes and retains recovery obligations after ambiguous or partial effects."],
  ["Can PACT reverse every external action?", "No. Compensation depends on the effect contract and the provider. Irreversible or unobservably restored effects can leave a HUMAN_REQUIRED obligation."],
  ["Does semantic review require an LLM?", "The reference service has deterministic checks and an optional independent model judge. Judge opinions are advisory; trusted code and authorized humans own commit authority."],
  ["Does wrapping my agent automatically protect its tools?", "No. Protected effects must go through registered workflows, contracts, and PACT-owned adapters. Direct provider credentials must be kept away from the agent."],
  ["Which interfaces exist today?", "The repository provides versioned REST endpoints, an MCP server, and a Python convenience client. It does not publish an SDK package or integrate arbitrary external applications automatically."],
  ["Is PACT production ready?", "The repository has local reference evidence, including simulator and isolated Git tests. Human semantic qualification and production provider adoption remain open."],
] as const;

export default function FaqPage() {
  return <section className="site-page-hero site-grid faq-page"><div className="site-container faq-grid">
    <div><Eyebrow>FAQ</Eyebrow><h1>Straight answers for teams building <em>agents.</em></h1>
      <p>Questions about boundaries, authority, recovery, and what this reference service actually supports.</p>
      <div className="faq-aside"><strong>Go deeper</strong><p>Explore the lifecycle, interfaces, and current limits in the documentation.</p><Link href="/docs">View documentation →</Link></div>
    </div><div className="faq-list">{questions.map(([question, answer], index) => <details key={question} open={index === 0}>
      <summary><span>{String(index + 1).padStart(2, "0")}</span><strong>{question}</strong><b aria-hidden="true">+</b></summary>
      <p>{answer}</p></details>)}</div>
  </div></section>;
}
