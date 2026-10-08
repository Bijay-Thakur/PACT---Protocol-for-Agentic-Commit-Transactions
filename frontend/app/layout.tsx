import type { Metadata } from "next";
import "./globals.css";
import "./site.css";
import { AppShell } from "@/components/AppShell";

export const metadata: Metadata = {
  title: { default: "PACT — Semantic transactions for agent systems", template: "%s · PACT" },
  description: "A transaction boundary for multi-step agent effects: review intent, enforce policy, coordinate dispatch, and verify outcomes.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body><AppShell>{children}</AppShell></body></html>;
}
