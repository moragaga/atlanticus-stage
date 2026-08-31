# Atlanticus R3.6 — Clean Integration & Operational Productization Handoff

**Version:** 1.0.0  
**Date:** 2026-08-31  
**Purpose:** working baseline for a new ChatGPT chat.

## Frozen state

- R3.5 Performance / Stress: **CLOSED — PASS/GREEN**
- F-010 Final Docker Qualification: **CLOSED — PASS/GREEN**
- Authoritative run: `09311e68`
- Qualified envelope: **E2 = 1 CPU / 2 GiB**
- Final workload: **1000 alarms / 1800 s / 5 s**
- 361/361 iterations, 0 overruns
- Durability/audit: PASS
- Management + Decisions: 480/480 + 480/480, exact lifecycle, no losses/duplicates
- Compatible adoption: 1000 COMPATIBLE, threshold 0.50 → 0.75
- Open product findings: **none**
- F-011: **Not Activated / Not Required**
- Azure controlled qualification: **not available / not active**

## Roadmap correction

The active next stage is **not Azure Deployment Qualification**.

Current R3.6 is:

> **Operational Productization & Clean Integration**

The goal is to converge:
1. the new clean/final Atlanticus structure and the contracts it already consumes; and
2. the validated Alarm Runtime + Alarm Process stack.

## Immediate priority

1. Inventory the clean repository.
2. Map the contracts already consumed.
3. Validate the current Alarm Runtime against the frozen R3.5 behavior.
4. Validate Alarm Process composition against that Runtime.
5. Classify modules as `KEEP`, `PRODUCTIZE`, `RETIRE`, `DEFER`, or `REWORK`.
6. Clean the repository and establish a green productive baseline.
7. Continue with missing operational capabilities.

## Missing / incomplete product capabilities

High priority:
- canonical Alarm Definition contract;
- Configuration source;
- Configuration resolver and canonical runtime-ready output;
- Configuration backend/UI;
- source acquisition contract;
- source providers/adapters (PI and optional declared sources);
- normalized Data Delivery;
- ManagementAction acquisition/delivery;
- end-to-end operational composition.

Deferred for now:
- acquisition of approved deactivation Decisions (runtime processing is already validated).

## What to upload in the new chat

Prefer ZIPs for source trees.

1. Current clean/final `atlanticus` repository or relevant subtree.
2. Repository tree/file listing if useful.
3. Contract packages consumed by the new structure.
4. Current Alarm Runtime source/package.
5. Current Alarm Process source/package.
6. Alarm Core + Persistence used by Process.
7. Relevant `pyproject.toml` files and `uv.lock`.
8. Current Runtime/Process tests and integration tests.
9. Current architecture/contracts/checkpoints for the new structure.
10. Temporary previews/harnesses still present.

The files uploaded in the new chat are authoritative for current versions and current source structure.

## First-pass procedure

- **Step 0:** ingest only; no modifications.
- **Step 1:** map contract ownership/dependencies.
- **Step 2:** diff current Runtime against the frozen validated behavior.
- **Step 3:** validate Process composition against current Runtime.
- **Step 4:** classify KEEP / PRODUCTIZE / RETIRE / DEFER / REWORK.
- **Step 5:** apply the smallest cleanup/integration increment.
- **Step 6:** run focused gate.
- **Step 7:** start the highest-value missing operational capability.

## Runtime / Process gates

Before joining both pieces:
- Runtime public contract is explicit and tested.
- Process composes Runtime/Core/Persistence and does not duplicate their behavior.
- Durability semantics remain compatible with the R3.5-qualified behavior.
- Natural drain/recovery remains covered.
- ManagementAction/Decision causal identity and receipt semantics remain preserved.
- Configuration reaches Runtime through a canonical validated Alarm Definition.
- Data reaches Process/Runtime through declared provider/delivery contracts.
- No temporary R3.5 harness is imported by productive code.
- No Azure/vendor-specific coupling is introduced into generic Atlanticus modules.

## Suggested sequence

- `R3.6A-001` — clean inventory + productive baseline freeze.
- `R3.6A-002` — Runtime contract reconciliation.
- `R3.6A-003` — Process composition reconciliation.
- `R3.6B-001` — canonical Alarm Definition contract.
- `R3.6B-002` — Configuration source/resolver/runtime-ready output.
- `R3.6B-003` — Configuration backend/UI.
- `R3.6C-001` — source requirement/provider composition.
- `R3.6C-002` — physical source acquisition + normalized delivery.
- `R3.6D-001` — ManagementAction acquisition/delivery.
- `R3.6D-002` — approved deactivation Decision acquisition when needed.
- `R3.6E-001` — final end-to-end operational composition + smoke.

These IDs are sequencing aids, not a reason to create unnecessary packages.

## Working rules

- Python 3.14.2 + UV. Never PIP.
- Atlanticus stays modular/reusable beyond ADA.
- Backend contracts before frontend.
- Explicit composition; avoid global clients and monolithic config.
- Only agreed changes + tests + Spanish commented mirror.
- No secrets in code/logs.
- No Azure-controlled qualification campaign now.
- No new long stress campaign without a concrete measurable risk.
- Prefer reuse/cleanup over new abstractions.

## Starter message

> Continuemos Atlanticus desde `Atlanticus_R36_Clean_Integration_Handoff_v1.0.0`. R3.5 está cerrada PASS/GREEN. No quiero iniciar una campaña Azure ni más stress por ahora. Adjunto la estructura limpia/final de Atlanticus, los contratos que ya estoy consumiendo y los paquetes actuales de Alarm Runtime/Process. Primero haz un inventario y compara los contratos/ownership, valida Runtime y Process contra la línea base R3.5 y clasifica cada pieza como KEEP / PRODUCTIZE / RETIRE / DEFER / REWORK. No hagas cambios hasta tener el mapa y detectar el primer gap real. Luego avanzamos por incrementos pequeños hasta unir la estructura limpia con el proceso de alarmas y completar Configuration, Sources/Delivery y Management integration.

## Definition of success

A clean authoritative Atlanticus repository where Alarm Runtime and Alarm Process compose through explicit validated contracts, temporary R3.5 artifacts are no longer architectural dependencies, and remaining work is driven by missing operational capabilities rather than test infrastructure.
