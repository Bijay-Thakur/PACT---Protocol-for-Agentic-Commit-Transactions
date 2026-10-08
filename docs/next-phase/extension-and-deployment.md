# Extension and protected deployment contract

## Workflow extension

A trusted, reviewed domain registration supplies:

- a versioned workflow and policy version;
- a closed Pydantic business-request schema;
- finite typed action slots, dependencies, and required outcomes;
- root capability/resource rules and deterministic compilation;
- optional trusted assembler registered through `AssemblyRegistry`;
- only reviewed invariant and outcome-predicate names.

`WorkflowRegistry.register` and `AssemblyRegistry.register` add domains without
new generic route branches. Runtime upload of Python, SQL, expressions, model
code, or untrusted plugins is prohibited.

## Adapter extension

An adapter registration owns a versioned Effect Contract, typed payload,
resource claims, provider/account binding, stable operation identity, read-only
prepare facts, dispatch, independent observation/reconciliation, and honest
compensation limits. It documents freshness/CAS behavior, idempotency horizon,
authoritative negative evidence, ambiguous-result handling, and residual duties.
Unsupported contract or predicate pins remain visible obligations; recovery
must never silently switch semantics.

Current configuration supports one account per simulated provider and tenant.
Ambiguous multi-account routing is unsupported and must be rejected, not
defaulted. `EffectRegistry` rejects a second adapter with the same effect type
(`AMBIGUOUS_ADAPTER_REGISTRATION`), and `WorkflowRegistry` rejects a second
workflow with the same key (`AMBIGUOUS_WORKFLOW_REGISTRATION`). Deployments
needing two accounts for one effect must add an explicit, versioned routing
contract before registration; order of registration cannot select an account.

## Protected deployment

The untrusted caller receives only a scoped PACT credential. It receives no
provider-write credential, protected Git mount, database credential, Docker
socket, or route to a protected provider endpoint. PACT's adapter process owns
the narrow write credential. A separate approver owns the required approval
scope. Logs and the browser contain no model or provider secrets.

The reference Compose declaration is not evidence by itself. The measured
local run used Docker 29.8.2 and published PACT only on `127.0.0.1:18080`.
The agent network cannot reach Postgres or the simulators. A healthy bare
repository started at one commit; the agent could not write it; a separate
approver accepted the digest; PACT promoted one file; the host then observed
two commits. That measurement is local reference evidence, not production
isolation or a Code Council integration.

Future Code Council and RivalScope work must first identify each application's
actual write/publish boundary, business identity, account, grants, credential
owner, observations, and recovery limits. This repository does not import or
integrate either application.
