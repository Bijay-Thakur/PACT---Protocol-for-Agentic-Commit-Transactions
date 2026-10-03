"use client";

import { useState, type ReactNode } from "react";
import { isInFlight, stateClasses } from "@/lib/states";
import type { Json } from "@/lib/types";

export function StateBadge({ state, size = "sm", title }: { state: string | null | undefined; size?: "xs" | "sm" | "lg"; title?: string }) {
  const c = stateClasses(state);
  const sz =
    size === "lg" ? "text-base px-4 py-1.5 font-semibold" : size === "xs" ? "text-xs px-2 py-0.5" : "text-xs px-2.5 py-1";
  return (
    <span
      title={title}
      className={`neu-chip inline-flex items-center gap-1.5 font-mono font-medium whitespace-nowrap ${c.badge} ${sz}`}
    >
      <span className={`h-2 w-2 rounded-full shadow-[0_0_0_2px_rgba(255,255,255,0.7)] ${c.dot} ${isInFlight(state) ? "animate-pulse" : ""}`} />
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
    <section className={`neu-raised ${className}`}>
      {(title || right) && (
        <header className="flex items-start justify-between gap-3 px-6 pt-5 pb-1">
          <div>
            {title && <h2 className="text-sm font-bold tracking-wide text-ink">{title}</h2>}
            {subtitle && <p className="mt-1 text-xs leading-relaxed text-mute">{subtitle}</p>}
          </div>
          {right}
        </header>
      )}
      <div className="p-6 pt-4">{children}</div>
    </section>
  );
}

export function Mono({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <span className={`font-mono text-[13px] break-all ${className}`}>{children}</span>;
}

export function KV({ k, children }: { k: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[150px_1fr] gap-2 py-0.5 text-xs">
      <div className="text-faint">{k}</div>
      <div className="min-w-0 text-ink">{children}</div>
    </div>
  );
}

export function JsonView({ value, maxHeight = 260 }: { value: Json | unknown; maxHeight?: number }) {
  return (
    <pre
      className="neu-inset-sm overflow-auto p-3 font-mono text-xs leading-relaxed text-ink-soft"
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
        className="rounded-md text-xs font-medium text-mute hover:text-accent"
      >
        {open ? "▾" : "▸"} {label}
      </button>
      {open && <div className="mt-1">{children}</div>}
    </div>
  );
}

export function PassMark({ passed }: { passed: boolean | null | undefined }) {
  if (passed === null || passed === undefined) return <span className="text-faint">–</span>;
  return passed ? (
    <span className="font-bold text-ok" aria-label="passed">
      ✓
    </span>
  ) : (
    <span className="font-bold text-bad" aria-label="failed">
      ✗
    </span>
  );
}

export function ErrorBox({ title, message }: { title: string; message: string }) {
  return (
    <div role="alert" className="neu-inset border-l-4 border-red-600 px-5 py-4 text-sm text-bad">
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
    <div className="neu-tabs" role="tablist">
      {tabs.map((t) => (
        <button
          key={t.key}
          type="button"
          role="tab"
          aria-selected={active === t.key}
          onClick={() => onChange(t.key)}
          className="neu-tab px-4 py-2 text-sm"
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}
