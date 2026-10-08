"use client";

import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { api, ApiError } from "@/lib/api";

export function AuthGate({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<"checking" | "signed_out" | "signed_in">("checking");
  const [name, setName] = useState("");
  const [activeTenant, setActiveTenant] = useState("");
  const [roles, setRoles] = useState<string[]>([]);
  const [tenant, setTenant] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.me().then((p) => {
      setName(p.name); setActiveTenant(p.tenant_id); setRoles(p.roles); setStatus("signed_in");
    })
      .catch(() => setStatus("signed_out"));
  }, []);

  async function signIn(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await api.login({ tenant_id: tenant, username, password });
      setName(result.principal.name);
      setActiveTenant(result.principal.tenant_id);
      const current = await api.me();
      setRoles(current.roles);
      setPassword("");
      setStatus("signed_in");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Unable to sign in");
    } finally {
      setBusy(false);
    }
  }

  if (status === "checking") return <div className="neu-inset p-5 text-sm text-mute">Checking session…</div>;
  if (status === "signed_out") return (
    <form onSubmit={signIn} className="neu-raised mx-auto max-w-md space-y-4 p-6">
      <h1 className="text-lg font-semibold">Operator sign in</h1>
      <p className="text-sm text-mute">Use an operator account provisioned on this PACT deployment.</p>
      <label className="block text-sm">Tenant
        <input className="neu-field mt-1 w-full p-2" value={tenant} onChange={(e) => setTenant(e.target.value)} required />
      </label>
      <label className="block text-sm">Username
        <input className="neu-field mt-1 w-full p-2" value={username} onChange={(e) => setUsername(e.target.value)} required />
      </label>
      <label className="block text-sm">Password
        <input className="neu-field mt-1 w-full p-2" type="password" value={password}
          onChange={(e) => setPassword(e.target.value)} required />
      </label>
      {error && <p role="alert" className="text-sm text-bad">{error}</p>}
      <button className="neu-btn neu-btn-primary px-4 py-2" disabled={busy} type="submit">
        {busy ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
  return <>
    <div className="mb-4 flex flex-wrap justify-end gap-x-3 text-xs text-mute">
      <span>Environment: {process.env.NEXT_PUBLIC_PACT_ENVIRONMENT || "local"}</span>
      <span>Tenant: {activeTenant}</span>
      <span>Role: {roles.length ? roles.join(", ") : "requester"}</span>
      <span>Signed in as {name}</span>
      <button className="ml-3 underline" onClick={() => api.logout().finally(() => setStatus("signed_out"))}>
        Sign out
      </button>
    </div>
    {children}
  </>;
}
