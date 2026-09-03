# AGENTS.md — runtime silo

Normative working rules for the VOX runtime layer in `src/vox/runtime/`. This is
agent instruction, not project documentation.

This document defines the **target architecture**: the boundary the runtime
layer must converge on. Workload rules are owned by
`src/vox/workloads/AGENTS.md`, capability rules by `src/vox/capabilities/AGENTS.md`,
and orchestration rules by `src/vox/orchestration/AGENTS.md`; those contracts are
referenced here, not duplicated. Existing code that violates the boundaries is
NON-CONFORMANT and is explicitly labeled as such, never treated as accepted
architecture.

## Responsibility

The runtime layer owns **process/bootstrap/composition concerns only**. It is
the process composition root: it wires the top-level components, runs the
daemon loop, and exposes the process-level control plane. It is **not** an
orchestrator, workload host, capability, configuration source, or security
component, and it must not re-implement their logic.

`VOXRuntime` is the **composition container** with no behavior of its own; it
holds the wired components (`config`, `logger`, `orchestrator`, `api_server`)
for the daemon to drive.

The runtime owns:

- composition/wiring of the process root (`factory.py` `build_vox`);
- the daemon bootstrap loop and process teardown (`daemon.py` `run_vox`);
- the Unix-domain-socket control plane and its request handlers
  (`control_plane.py`);
- the `VOXRuntime` composition container (`models.py`).

## Modules

- `factory.py` — `build_vox(config, logger)`: the composition root. Constructs
  the concrete `VOXOrchestrator` and `VOXAPIServer`, then the `VOXRuntime`
  container. No domain logic; wiring only.
- `daemon.py` — `run_vox(runtime, logger)`: process bootstrap. Boots the
  orchestrator, starts the API server, runs the keepalive loop and the control
  plane, and tears down the composed components on exit.
- `control_plane.py` — UDS socket transport. Parses JSON control commands and
  drives fleet operations through the orchestrator's **public** surface only.
- `models.py` — `VOXRuntime`: the composition container dataclass.

## Boundaries

### Runtime → external components

The runtime consumes the concrete `VOXOrchestrator`, `VOXAPIServer`, and
`VOXConfig` it composes. Interaction with them must go through their **public**
methods and properties only. The runtime must never read or mutate their
privates. It is a consumer of these components' public contracts, not an
authority over their internals.

- `daemon.py` uses `orchestrator.boot()` / `orchestrator.shutdown()` and
  `api_server.start()` / `api_server.shutdown()` — public lifecycle methods.
- `control_plane.py` uses `get_fleet_snapshot`, `resolve_workload_id`,
  `stop_workload`, `restart_workload`, `start_workload_by_name`,
  `pause_workload`, `resume_workload` — public fleet operations.
- Runtime code must not reach `_orchestrator`-style privates or any workload or
  capability private state.

### Configuration

The runtime is **not a configuration authority**. Process configuration lives
in `vox.config` (`VOXConfig`). The runtime consumes `VOXConfig` for the pieces
it needs (e.g. `uds_path`). Do not introduce a parallel runtime configuration
dataclass that mirrors fields already owned by `VOXConfig` — that duplicates
ownership and is NON-CONFORMANT. Runtime-specific tuning that has no real
consumer must not be declared as a phantom config.

### Dependency direction / imports

- Submodules must import from the concrete leaf modules (`vox.runtime.daemon`,
  `vox.runtime.models`, ...), **never** from the package `__init__` — importing
  from the package `__init__` re-enters the public surface and creates the
  circular chain that required import-order hacks. Importing from the leaf
  submodule is acyclic and is the target form.
- The package `__init__` may import its submodules alphabetically; no ordering
  constraint or `# noqa: I001` should be needed once submodules stop importing
  from `__init__`.

## Architectural invariants (target)

1. The runtime owns process/bootstrap/composition concerns only — no
   orchestration, workload, capability, configuration, or security logic.
2. `VOXRuntime` is a pure composition container with no behavior.
3. Runtime interacts with `VOXOrchestrator`/`VOXAPIServer` through their public
   surface only — never their or any workload/capability privates.
4. Configuration is owned by `vox.config`; no parallel runtime config dataclass.
5. Runtime submodules import from leaf submodules, never from the package
   `__init__` (acyclic imports).

## Known boundary debt (current vs. target)

- **Out-of-silo (reported, not fixed here):** `src/vox/api_server.py:60`
  (`VOXAPIServer._guardrail_middleware`) reaches `self._orc._guardrail.sanitize`
  — the orchestrator's private guardrail. The API server is consumed by the
  runtime factory and lives on the `VOXRuntime` container, but the access
  violates the orchestrator's public boundary and should be fixed from the
  api-server/orchestration side (e.g. expose a public sanitize/guardrail path on
  `VOXOrchestrator`). It is in-scope-ownership debt for the API server silo, not
  the runtime silo.
- **Deferred (behavior-preserving):** `daemon.py` hardcodes the keepalive
  interval (`3600`). No runtime config currently drives it; exposing it as
  config warrants a `VOXConfig` addition and is a deliberate change, not part of
  this boundary audit.

## Testing / architecture enforcement

- `pytest` (`python -m pytest`, asyncio auto mode). Behavior changes ship with
  tests in `tests/test_*.py`.
- `tests/test_runtime_arch.py` guards the silo: leaf-submodule imports
  (no package-`__init__` re-entry), no runtime config duplication vs `VOXConfig`,
  no private-state reach, and the composition-root-only role of `models.py`.
- The runtime silo's dependencies on the orchestration/api-server public
  surfaces are verified by the boundary tests above.

## What constitutes an architectural change

Any of the following must be proposed and reviewed as an architectural change:

- adding a new runtime-owned service that is not process/bootstrap/composition
  in nature;
- introducing runtime configuration that duplicates `VOXConfig`;
- re-establishing package-`__init__` re-entry imports in runtime submodules;
- widening the runtime's reach into orchestration/api-server internals.