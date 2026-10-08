"use client";

import { useState } from "react";

const examples = [
  { label: "REST API", name: "discover.sh", code: [
    "# Set PACT_BASE_URL and a scoped PACT_API_KEY first.",
    'curl -H "Authorization: Bearer $PACT_API_KEY" "$PACT_BASE_URL/api/v1/workflows"',
    "# Response: workflows available to this authenticated principal.",
  ].join("\n") },
  { label: "Python client", name: "discover.py", code: [
    "# From this repository: pip install -e backend",
    "import asyncio", "import httpx", "from app.agents.client import HttpPactClient", "",
    "async def main():",
    '    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000") as http:',
    '        pact = HttpPactClient(http, api_key="YOUR_SCOPED_KEY")',
    "        print(await pact.list_workflows())", "", "asyncio.run(main())",
  ].join("\n") },
  { label: "MCP", name: "stdio configuration", code: [
    "# From backend/ with the local Python environment:",
    "# Configure PACT_API_URL and PACT_MCP_API_KEY in the process environment.",
    "python -m app.mcp_server", "",
    "# MCP exposes agent-safe tools. Operator approval remains REST/UI-only.",
  ].join("\n") },
] as const;

export function CodeTabs() {
  const [active, setActive] = useState(0);
  const [copied, setCopied] = useState(false);
  const example = examples[active];
  async function copy() {
    try { await navigator.clipboard.writeText(example.code); setCopied(true); setTimeout(() => setCopied(false), 2000); }
    catch { setCopied(false); }
  }
  return <div className="code-tabs"><div className="code-tabs-nav" role="tablist" aria-label="Integration interface">
    {examples.map((item, index) => <button key={item.label} type="button" role="tab" aria-selected={active === index}
      onClick={() => { setActive(index); setCopied(false); }}>{item.label}</button>)}
  </div><div className="code-tabs-panel" role="tabpanel"><div className="code-tabs-meta"><span>{example.name}</span>
      <button type="button" onClick={copy}>{copied ? "Copied" : "Copy code"}</button></div>
      <pre><code>{example.code}</code></pre></div></div>;
}
