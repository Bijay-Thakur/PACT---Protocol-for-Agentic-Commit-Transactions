import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import "./globals.css";
import { AuthGate } from "@/components/AuthGate";

export const metadata: Metadata = {
  title: "PACT Console",
  description: "The commit & accountability layer for multi-agent operations",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-neu text-ink antialiased">
        <div className="mx-auto max-w-[1600px] px-3 pt-3 sm:px-6 sm:pt-5">
          <header className="neu-raised">
            <div className="flex flex-wrap items-center gap-3 px-4 py-3.5 sm:px-6">
              <Link href="/" className="flex items-center gap-4 rounded-lg" aria-label="PACT console home">
                <Image
                  src="/Assets/Images/pact-logo.png"
                  alt="PACT"
                  width={916}
                  height={275}
                  preload
                  className="h-11 w-auto"
                />
                <span className="hidden max-w-[22rem] text-xs leading-snug text-mute sm:inline">
                  The commit &amp; accountability layer for multi-agent operations
                </span>
              </Link>
              <nav aria-label="Primary" className="ml-auto flex flex-wrap items-center justify-end gap-2 text-xs text-mute">
                {[
                  ["/", "Overview"],
                  ["/transactions", "Transactions"],
                  ["/requests/new", "New request"],
                  ["/approvals", "Approvals"],
                  ["/incidents", "Incidents"],
                  ["/receipts", "Receipts"],
                  ["/demo", "Demo"],
                ].map(([href, label]) => (
                  <Link key={href} href={href} className="neu-btn px-3 py-2 text-xs">{label}</Link>
                ))}
                <span className="neu-chip hidden px-3 py-1.5 font-mono text-faint md:inline">
                  {process.env.NEXT_PUBLIC_PACT_API_URL || "http://localhost:8000"}
                </span>
              </nav>
            </div>
          </header>
        </div>
        <main className="mx-auto max-w-[1600px] px-3 py-4 sm:px-6 sm:py-6"><AuthGate>{children}</AuthGate></main>
      </body>
    </html>
  );
}
