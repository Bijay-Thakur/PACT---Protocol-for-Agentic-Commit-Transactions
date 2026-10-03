import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "PACT Console",
  description: "The commit & accountability layer for multi-agent operations",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-zinc-950 text-zinc-200 antialiased">
        <header className="border-b border-zinc-800 bg-zinc-950/90 backdrop-blur">
          <div className="mx-auto flex max-w-[1600px] items-center gap-4 px-6 py-3">
            <Link href="/" className="flex items-baseline gap-3">
              <span className="font-mono text-lg font-bold tracking-widest text-zinc-50">PACT</span>
              <span className="hidden text-xs text-zinc-400 sm:inline">
                The commit &amp; accountability layer for multi-agent operations
              </span>
            </Link>
            <nav className="ml-auto flex items-center gap-4 text-xs text-zinc-400">
              <Link href="/" className="hover:text-zinc-100">
                Scenarios &amp; transactions
              </Link>
              <span className="font-mono text-zinc-600">{process.env.NEXT_PUBLIC_PACT_API_URL || "http://localhost:8000"}</span>
            </nav>
          </div>
        </header>
        <main className="mx-auto max-w-[1600px] px-6 py-5">{children}</main>
      </body>
    </html>
  );
}
