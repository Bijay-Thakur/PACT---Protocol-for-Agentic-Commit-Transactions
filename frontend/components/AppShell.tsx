"use client";

import { useState, type ReactNode } from "react";
import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { AuthGate } from "@/components/AuthGate";

const publicLinks = [
  ["/", "Overview"], ["/semantic-transactions", "Semantic Transactions"],
  ["/product", "Product"], ["/integrate", "Integrate"],
  ["/compare", "Compare"], ["/docs", "Docs"], ["/faq", "FAQ"],
] as const;
const consoleLinks = [
  ["/console", "Overview"], ["/transactions", "Transactions"],
  ["/requests/new", "New request"], ["/approvals", "Approvals"],
  ["/incidents", "Incidents"], ["/receipts", "Receipts"], ["/demo", "Demo Mode"],
] as const;
const publicPaths = new Set([...publicLinks.map(([href]) => href), "/get-started"]);

function Brand() {
  return <Link href="/" className="brand" aria-label="PACT home">
    <Image src="/Assets/Images/pact-mark.png" alt="" width={289} height={270} className="brand-mark" priority />
    <span>PACT</span>
  </Link>;
}

function PublicHeader({ path }: { path: string }) {
  const [open, setOpen] = useState(false);
  return <header className="site-header"><div className="site-container site-header-inner">
    <Brand />
    <button className="site-menu-button" type="button" aria-label="Toggle navigation" aria-expanded={open}
      onClick={() => setOpen((current) => !current)}>{open ? "Close" : "Menu"}<span aria-hidden="true">☰</span></button>
    <nav aria-label="Site" className={`site-nav ${open ? "site-nav-open" : ""}`}>
      {publicLinks.map(([href, label]) => <Link key={href} href={href} aria-current={path === href ? "page" : undefined}
        onClick={() => setOpen(false)}>{label}</Link>)}
      <Link className="site-nav-mobile-action" href="/console" onClick={() => setOpen(false)}>Open Console →</Link>
    </nav>
    <div className="site-header-actions"><Link className="site-text-link" href="/demo">Demo Mode</Link>
      <Link className="site-button site-button-small" href="/console">Open Console <span aria-hidden="true">→</span></Link></div>
  </div></header>;
}

function PublicFooter() {
  return <footer className="site-footer"><div className="site-container site-footer-inner">
    <div><Brand /><p>Semantic transaction control for consequential agent effects.</p></div>
    <div className="site-footer-links"><Link href="/semantic-transactions">Concepts</Link>
      <Link href="/integrate">Integrate</Link><Link href="/docs">Docs</Link>
      <Link href="/demo">Demo Mode</Link><Link href="/get-started">Get Started</Link></div>
  </div></footer>;
}

function ConsoleHeader() {
  return <header className="console-header"><div className="console-header-inner">
    <div className="console-header-top"><Brand /><Link href="/" className="console-back">← Product site</Link></div>
    <nav aria-label="Primary" className="console-nav">
      {consoleLinks.map(([href, label]) => <Link key={href} href={href}>{label}</Link>)}
    </nav>
  </div></header>;
}

export function AppShell({ children }: { children: ReactNode }) {
  const path = usePathname();
  if (publicPaths.has(path)) return <div className="site">
    <PublicHeader path={path} /><main id="main-content">{children}</main><PublicFooter />
  </div>;
  return <div className="console-shell"><ConsoleHeader />
    <main id="main-content" className="console-main"><AuthGate>{children}</AuthGate></main>
  </div>;
}
