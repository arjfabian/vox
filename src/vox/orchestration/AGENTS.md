# AGENTS.md — orchestration silo

Normative working rules for the VOX orchestration layer in
`src/vox/orchestration/`. This is agent instruction, not project documentation.

This document defines the **target architecture** the orchestration layer must
converge on, and the boundary it must not cross. Workload-specific interfaces
are owned by `src/vox/workloads/AGENTS.md` and capability rules by
`src/vox/capabilities/AGENTS.md`; those contracts are referenced here, not
duplicated. Existing code that conflicts with those contracts is
NON-CONFORMANT and must be brought into line, never treated as accepted
architecture.

## Responsibility

Orchestration owns **routing policy and fleet-level coordination**. It is a
**consumer of the workload contract, never of workload private implementation
details.** Orchestration is not a workload-host implementation and must not
re-implement workload-local mechanics.

Orchestration owns:

- capability and workload discovery/hiring (`VOXRegistry`);
- fleet topology and identity resolution (`FleetGraph`);
- fleet-level lifecycle coordination (`FleetController`);
- inbound routing policy: guardrail sanitization, alert-vs-workload targeting,
  and fan-out to mounted workloads (`dispatch_inbound_message`);
- the war room alert bus and its dispatcher (`VOXWarRoom` / `VOXWarRoomMaster`);
- hot-reload of workloads via file watching (`WorkloadFileWatcher`);
- panic shutdown coordination across the fleet.

## Roles

- `base.py` — `VOXOrchestrator` facade: wires the service components, implements
  the provider-side inbound dispatch, and exposes fleet operations. It is the
  concrete `CapabilityProviderProtocol` the workload consumes.
- `registry.py` — `VOXRegistry`: capability discovery/introspection and workload
  hire, using the **public** workload contract (`degraded`, `health_check`,
  `boot`) — never workload privates.
- `graph.py` — `FleetGraph`: read-only index over the fleet registries and
  hierarchy queries. Reads public workload identity/`describe()` only.
- `controller.py` — `FleetController`: lock-guarded fleet lifecycle transitions
  (stop/start/restart/pause/resume). Uses public workload APIs; never
  `workload._degraded`.
- `war_room.py` — `VOXWarRoom` incident queue + `VOXWarRoomMaster` alert
  dispatcher. Fans out via `workload.emit(...)` only.
- `watcher.py` — `WorkloadFileWatcher`: triggers hot-restart through public
  orchestrator operations.

## Inbound routing semantics

`dispatch_inbound_message(source, payload) -> bool` is the provider-side routing
authority. Routing policy lives **here**, never in workloads:

1. Sanitize the payload through the guardrail; on `SecurityError` return
   `False` (rejected).
2. `type == "alert"` → publish to the war room; return `True`.
3. Otherwise → fan out to the mounted workload that declares `source` in its
   capabilities via `workload.emit("inbound_message", ...)`; return whether a
   target received it.

The workload's `VOXWorkload.dispatch_inbound(source, payload)` is a thin
transport delegate to this provider method. Do not duplicate routing logic on
the workload side or route directly from orchestration into workload internals.

## Boundaries with workloads and provider

- **Workload boundary**: orchestration is a consumer of the public workload
  contract only. It uses `boot`/`pause`/`resume`/`stop`/`shutdown`, `state`,
  `health_check`, `degraded`, `emit`, `name`, `id`, `master_id`, `dir`, `vault`,
  `describe`, and any public workload-owned teardown method. It must **never**
  access `_degraded`, `_tasks`, `_vault`, `vault._key`, `_capability_commands`,
  or any other workload/`WorkloadVault` private.
- **Lifecycle/resource operations** (task teardown, secret destruction, vault
  handling, panic-shutdown mechanics) are workload-owned. Orchestration must
  request them through public workload-owned APIs — e.g. `cancel_tasks()` and
  `purge_vault()` for the panic path — never by touching workload internals.
- **Provider boundary**: orchestration is the concrete
  `CapabilityProviderProtocol`. Provider interaction must remain protocol-based
  and minimal. Do not treat the current `CapabilityProviderProtocol` surface as
  immutable; future work should shrink it where possible. Do not reach past the
  protocol into capability or workload implementation details.

## Forbidden workload-private access

Orchestration must not reference or mutate: `_degraded`, `_tasks`, `_vault`,
`vault._key`, `_capability_commands`, `_system_capabilities`, `_event_router`,
`_roles`, or any other private. If a needed service is private, add a public
workload-owned API in the workload silo (minimal change, reported) — never reach
for the private from orchestration.

## Architectural invariants (target)

1. Orchestration owns routing policy and fleet-level coordination; workloads
   own workload-local runtime mechanics.
2. Orchestration consumes only the public workload contract — never workload or
   `WorkloadVault` privates.
3. Lifecycle/resource/panic operations are requested via public workload-owned
   APIs (`cancel_tasks`, `purge_vault`, `boot`, `stop`, `shutdown`, ...).
4. Inbound routing and guardrail/alert/fan-out policy live entirely in
   orchestration/provider, never in workloads.
5. Orchestration implements the provider side of the protocols in `vox/provider.py`
   and does not import capability internals or reach concrete workload state.

## What constitutes an architectural change

Any of the following must be proposed and reviewed as an architectural change,
not a routine edit:

- widening/reducing `CapabilityHostProtocol` or `CapabilityProviderProtocol`;
- changing inbound routing or guardrail/alert/fan-out semantics;
- moving responsibility for lifecycle, state, or resource management between
  orchestration and the workload silo;
- adding a new workload-owned public API required by orchestration;
- crossing into capability or provider internals.

## Testing / architecture enforcement

- `pytest` (`python -m pytest`, asyncio auto mode). Behavior changes ship with
  tests in `tests/test_*.py`.
- `tests/test_orchestration_arch.py` guards the silo: orchestration code must not
  import or reach workload/`WorkloadVault` privates, and must prefer the public
  `degraded` accessor.
- `tests/test_workloads_arch.py` and `tests/test_capability_arch.py` guard the
  workload and capability silos respectively.

## Known boundary debt (current vs. target)

The workload `AGENTS.md` previously recorded orchestration reading workload
privates as non-conformant. That debt is now closed: the panic path uses
`cancel_tasks()` / `purge_vault()`, and `_degraded` reads use the public
`degraded` accessor. Keep it that way; do not reintroduce private access.
