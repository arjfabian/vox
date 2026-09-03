# AGENTS.md — workloads silo

Normative working rules for the VOX workload runtime layer in
`src/vox/workloads/`. This is agent instruction, not project documentation.

This document defines the **target architecture**: the boundary the workload
layer must converge on, not a record of every current internal. Where the
current code still violates the target, that is called out explicitly as
non-conformant and to be removed/closed, never treated as accepted design.
Capability-side rules are owned by `src/vox/capabilities/AGENTS.md` and only
referenced here; they are not duplicated.

## Responsibility

The workload layer defines the technical runtime for one persona and is the
**sole host implementation** of `CapabilityHostProtocol`. `VOXWorkload` is the
owner of workload-local state and of the mechanics of lifecycle and resource
management. `VOXWorkload` owns:

- workload-local configuration (loaded from `manifest.yml` via
  `WorkloadLoader`), identity, and state;
- the per-workload `WorkloadVault`, including secret destruction and vault
  lifecycle;
- mounted capabilities and the capability registry;
- explicit roles (`VOXRole` subclasses) and the event router;
- lifecycle state transitions and task/resource teardown (`WorkloadState`,
  running-task management);
- the private persistent workspace (`VOXWorkloadStore`) and the append-only
  forensic ledger (`VOXWorkloadMemory`);
- degradation state and role disabling.

`VOXWorkload` is a **transport/host boundary for inbound traffic, not a routing
authority**. It owns the inbound entry point but not the routing policy.

## Modules

- `base.py` — `VOXWorkload`, the `CapabilityHostProtocol` implementation.
- `capability_binder.py` — `CapabilityBinder`: mounts capabilities from
  contracts and binds Vault secrets. Composition mechanism; depends only on
  `CapabilityHostProtocol`, never concrete workload privates.
- `ast_analyzer.py` — `ASTWorkloadAnalyzer`: static scanner of role files for
  `REQUIRES` / `REQUIRED_SECRETS` (no code execution).
- `loader.py` — `WorkloadLoader` (manifest/.env parsing, validation, identity)
  plus `WorkloadProvisionError`.
- `lifecycle.py` — `WorkloadState` transitions and `EventQueue`.
- `memory.py` — `VOXWorkloadMemory` (forensic audit log; not semantic memory).
- `store.py` — `VOXWorkloadStore` (isolated SQLite workspace + asset sandbox).

## Boundaries

### Inbound (workload → provider)

The workload is a transport/host boundary, **not a routing authority**. Its
inbound path delegates outward; routing policy (guardrail sanitization, alert
vs. workload targeting, fan-out) lives outside the workload. Do not duplicate
routing/dispatch logic in the workload layer.

The workload never imports or reaches into orchestration internals. Its outward
coupling is limited to the protocol abstractions in `vox/provider.py` and must
remain protocol-based and minimal. Do not treat the current
`CapabilityProviderProtocol` surface as immutable: it exists only to cover
current needs, and future work should shrink it further where possible.

- `CapabilityHostProtocol` — the surface the workload provides to the
  capability/binder side (identity, vault, registry, `dispatch_inbound`,
  `get_safe_path`, `mark_degraded`, `disable_roles`,
  `register_capability_command`).
- `CapabilityProviderProtocol` — the narrow, minimal provider surface the
  workload consumes when it must reach outward (`get_capability_instance`,
  `get_children`, `dispatch_inbound_message`).

`dispatch_inbound(source, payload) -> bool` is the single capability-facing
inbound entry point. It delegates to the provider dispatcher and absorbs
`SecurityError` to return a boolean; it does not decide where a message goes.

### Outbound (orchestration → workload)

`VOXWorkload` is the owner of its host services, workload-local state, and the
mechanics of lifecycle and resource management. External layers (the
orchestrator/fleet) must request lifecycle/resource operations exclusively
through public workload-owned APIs — `boot` / `pause` / `resume` / `stop` /
`shutdown`, the `state` property, `health_check()`, the read-only `degraded`
accessor, and any public workload-owned method that encapsulates a currently
private service.

External layers must never read or mutate workload-private state: `_degraded`,
`_tasks`, `_vault`, `vault._key`, `_capability_commands`, or any similar
private. If a consumer needs a workload service that is currently private, add a
public workload-owned method — never reach for the private attribute from
outside the silo.

Secret destruction, vault operations, task/resource teardown, and panic-shutdown
mechanics are **workload-owned**. Orchestration must request these through
public workload-owned APIs and must never touch `_vault`, `vault._key`, `_tasks`,
or equivalent internals to effect them.

## Workload state / lifecycle rules

- Degradation state is workload-owned. The host protocol mutates it via
  `mark_degraded()` (called by the binder when mounting fails and by the
  bootstrap when no roles are active); external layers read it through the
  public read-only `degraded` accessor, never `_degraded`.
- State transitions go through the `WorkloadState` machine (enforced by the
  `state` property setter). Do not bypass it.
- `get_safe_path(sub_dir, filename)` resolves capability-owned resources inside
  the workload `assets/` sandbox and rejects escapes (`PermissionError`).
- Secrets are held only in the workload `WorkloadVault` and injected into bound
  capability `_secrets` during boot; never in source, tests, or logs. Secret
  destruction is a workload-owned operation.
- Capability mounting requires an explicit declaration: never auto-discover.
  Required capabilities come from `REQUIRES` in a role (AST-scanned); secrets
  come from `REQUIRED_SECRETS` and the YAML contract's `required: true` flags.
- Optional credentials default to `""`; `None` means "required — DEGRADED".
  This convention is decided in the capability silo and enforced by the binder.

## Architectural invariants (target)

1. `VOXWorkload` is the only `CapabilityHostProtocol` implementation.
2. `CapabilityBinder` and `VOXBoundCapability` depend only on
   `CapabilityHostProtocol`; they never reach concrete workload privates.
3. The workload layer imports only protocol abstractions from `vox.provider`
   for its outward coupling — never `vox.orchestration`.
4. Degradation state is read through the public `degraded` accessor.
5. The workload is a transport/host boundary, not a routing authority: inbound
   routing policy lives outside the workload.
6. Secret destruction, vault operations, task/resource teardown, and
   panic-shutdown mechanics are workload-owned; external layers request them
   through public workload-owned APIs.
7. Configuration metadata is YAML-authoritative (capability side) and the
   binder validates overrides against contracts; there is no legacy fallback.

## Testing / architecture enforcement

- `pytest` (`python -m pytest`, asyncio auto mode). Behavior changes ship with
  tests in `tests/test_*.py`.
- `tests/test_workloads_arch.py` guards the silo: degradation-encapsulation
  invariants and the no-orchestration-import boundary.
- `tests/test_capability_arch.py` guards the capability side (which the
  workload host implements).

## Known boundary debt (current vs. target)

The orchestration layer currently reads workload-private state directly and
that is **explicitly NON-CONFORMANT legacy code**, not accepted architecture:

- `registry.py` / `controller.py` read `workload._degraded`;
- `orchestration/base.py` panic shutdown reads `workload._tasks` and
  `workload._vault` / `vault._key`.

These must be removed/fixed during the orchestration audit by routing the
operations through public workload-owned APIs (e.g. lifecycle, task teardown,
and vault/secret-destruction methods owned by `VOXWorkload`). They are outside
the workload silo and must not be replicated. Fix them from the orchestration
side, proposing the public workload-owned APIs as part of that change — not by
openly touching workload privates, and not by unilaterally editing orchestration
files from within the workload silo.
