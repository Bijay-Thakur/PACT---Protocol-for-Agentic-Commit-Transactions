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
        <div className="mx-auto max-w-[1600px] px-6 pt-5">
          <header className="neu-raised">
            <div className="flex items-center gap-4 px-6 py-3.5">
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
              <nav className="ml-auto flex items-center gap-4 text-xs text-mute">
                <Link href="/" className="neu-btn px-4 py-2 text-xs">
                  Scenarios &amp; transactions
                </Link>
                <span className="neu-chip hidden px-3 py-1.5 font-mono text-faint md:inline">
                  {process.env.NEXT_PUBLIC_PACT_API_URL || "http://localhost:8000"}
                </span>
              </nav>
            </div>
          </header>
        </div>
        <main className="mx-auto max-w-[1600px] px-6 py-6"><AuthGate>{children}</AuthGate></main>
      </body>
    </html>
  );
}
