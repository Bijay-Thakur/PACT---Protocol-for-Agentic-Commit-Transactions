"use client";

import { useState, type ReactNode } from "react";
import { isInFlight, stateClasses } from "@/lib/states";
import type { Json } from "@/lib/types";

export function StateBadge({ state, size = "sm", title }: { state: string | null | undefined; size?: "xs" | "sm" | "lg"; title?: string }) {
  const c = stateClasses(state);
  const sz =
    size === "lg" ? "text-base px-3 py-1 font-semibold" : size === "xs" ? "text-[10px] px-1.5 py-0" : "text-xs px-2 py-0.5";
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1.5 rounded-md ring-1 ring-inset font-mono whitespace-nowrap ${c.badge} ${sz}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${c.dot} ${isInFlight(state) ? "animate-pulse" : ""}`} />
      {state ?? "—"}
    </span>
  );
}

export function Card({
  title,
  subtitle,
  right,
  children,
  className = "",
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-lg border border-zinc-800 bg-zinc-900/60 ${className}`}>
      {(title || right) && (
        <header className="flex items-start justify-between gap-3 border-b border-zinc-800 px-4 py-2.5">
          <div>
            {title && <h2 className="text-sm font-semibold tracking-wide text-zinc-100">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-zinc-400">{subtitle}</p>}
          </div>
          {right}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Mono({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <span className={`font-mono text-[12px] break-all ${className}`}>{children}</span>;
}

export function KV({ k, children }: { k: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[150px_1fr] gap-2 py-0.5 text-xs">
      <div className="text-zinc-500">{k}</div>
      <div className="min-w-0 text-zinc-200">{children}</div>
    </div>
  );
}

export function JsonView({ value, maxHeight = 260 }: { value: Json | unknown; maxHeight?: number }) {
  return (
    <pre
      className="overflow-auto rounded bg-black/50 p-2 font-mono text-[11px] leading-relaxed text-zinc-300"
      style={{ maxHeight }}
    >
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

export function Expandable({ label, children, defaultOpen = false }: { label: ReactNode; children: ReactNode; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="text-[11px] text-zinc-400 hover:text-zinc-200"
      >
        {open ? "▾" : "▸"} {label}
      </button>
      {open && <div className="mt-1">{children}</div>}
    </div>
  );
}

export function PassMark({ passed }: { passed: boolean | null | undefined }) {
  if (passed === null || passed === undefined) return <span className="text-zinc-500">–</span>;
  return passed ? (
    <span className="font-bold text-emerald-400" aria-label="passed">
      ✓
    </span>
  ) : (
    <span className="font-bold text-red-400" aria-label="failed">
      ✗
    </span>
  );
}

export function ErrorBox({ title, message }: { title: string; message: string }) {
  return (
    <div className="rounded-md border border-red-800 bg-red-950/40 px-4 py-3 text-sm text-red-200">
      <div className="font-semibold">{title}</div>
      <div className="mt-1 font-mono text-xs">{message}</div>
    </div>
  );
}

export function Tabs<T extends string>({
  tabs,
  active,
  onChange,
}: {
  tabs: { key: T; label: ReactNode }[];
  active: T;
  onChange: (k: T) => void;
}) {
  return (
    <div className="flex gap-1 border-b border-zinc-800">
      {tabs.map((t) => (
        <button
          key={t.key}
          type="button"
          onClick={() => onChange(t.key)}
          className={`-mb-px border-b-2 px-3 py-2 text-sm ${
            active === t.key ? "border-indigo-400 text-zinc-100" : "border-transparent text-zinc-400 hover:text-zinc-200"
          }`}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}
