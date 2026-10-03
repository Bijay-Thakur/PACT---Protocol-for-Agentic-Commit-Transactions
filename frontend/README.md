# PACT operator console

Next.js (App Router) + Tailwind + React Flow. Renders live backend state only. It never invents statuses.

```bash
npm install
NEXT_PUBLIC_PACT_API_URL=http://localhost:8000 npm run dev     # http://localhost:3000
npm run build                                                   # standalone output (see Dockerfile)
```

- `/`: scenario launcher (preconfigured fault injection, live pacing, pause-at-UNKNOWN) and the transaction list
- `/tx/[id]`: commit barrier, agent hierarchy (local vs global validity), effect DAG, per-effect *provider said vs reality verified*, authority/exposure, invariants, ground-truth external state, live event timeline (SSE), operator actions
- `/tx/[id]/receipt`: receipt with hash verification and raw JSON (drafts shown for non-terminal transactions)

After `npm run build` with `output: "standalone"`, run `node .next/standalone/server.js` after copying `public/` and `.next/static/` into the standalone folder, as the Dockerfile does.
