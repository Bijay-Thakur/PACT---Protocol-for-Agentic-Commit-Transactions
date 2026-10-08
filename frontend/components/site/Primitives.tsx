import Link from "next/link";
import type { ReactNode } from "react";

export function Eyebrow({ children }: { children: ReactNode }) {
  return <p className="eyebrow">{children}</p>;
}

export function SectionHeading({ kicker, title, text }: { kicker: string; title: ReactNode; text: string }) {
  return <div className="site-section-heading"><div><Eyebrow>{kicker}</Eyebrow><h2>{title}</h2></div><p>{text}</p></div>;
}

export function SiteCta({ title, text, primaryHref, primary, secondaryHref, secondary }: {
  title: string; text: string; primaryHref: string; primary: string; secondaryHref: string; secondary: string;
}) {
  return <section className="site-cta"><div className="site-container site-cta-inner">
    <div><Eyebrow>EXPLORE THE SYSTEM</Eyebrow><h2>{title}</h2><p>{text}</p></div>
    <div className="site-actions"><Link className="site-button" href={primaryHref}>{primary} <span aria-hidden="true">→</span></Link>
      <Link className="site-button site-button-outline" href={secondaryHref}>{secondary}</Link></div>
  </div></section>;
}
