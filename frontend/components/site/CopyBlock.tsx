"use client";

import { useState } from "react";

export function CopyBlock({ label, code }: { label: string; code: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try { await navigator.clipboard.writeText(code); setCopied(true); setTimeout(() => setCopied(false), 2000); }
    catch { setCopied(false); }
  }
  return <div className="copy-block"><div><span>{label}</span><button type="button" onClick={copy}>{copied ? "Copied" : "Copy code"}</button></div>
    <pre><code>{code}</code></pre></div>;
}
