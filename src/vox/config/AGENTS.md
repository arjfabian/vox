# AGENTS.md — config silo

Normative working rules for the VOX configuration layer in `src/vox/config/`.
This is agent instruction, not project documentation. It defines the **target
architecture**: the boundary configuration must converge on, not a record of
every current internal. Existing code that violates the boundary is NON-CONFORMANT
and is explicitly labeled as such, never treated as accepted architecture.

## Responsibility

The config silo is the **authoritative owner of process/daemon configuration** for
one VOX process. It owns the configuration model and defaults, the CLI and
environment input sources, parsing/type conversion, precedence resolution,
validation, and loading/merging into a single resolved `VOXConfig`.

It is **not** the owner of, and must not absorb:

- **workload configuration** — per-persona `manifest.yml` + `.env`, owned by the
  workloads silo (`WorkloadLoader`) and validated per capability contract;
- **capability configuration** — `capability.yml` contracts and per-adapter
  `config.yml`, owned by the capabilities silo;
- **Vault secrets** — resolution from the workload `WorkloadVault`, owned by the
  security/workloads silos. Process config carries **secret references** (e.g.
  environment-backed values), never resolved secret material.

## Modules

- `models.py` — `VOXConfig` (the resolved process-config model) and the module
  defaults (`DEFAULT_VERBOSE_LOGGING`, `DEFAULT_LOG_PATH`, `DEFAULT_UDS_PATH`).
  DEFAULTS HAVE SINGLE OWNERSHIP HERE. Other modules/domains must import a
  default from here rather than re-declaring the literal.
- `from_cli.py` — `VOXCliArgs` + `load_cli_args()`: argparse front-end. Raw CLI
  input; no precedence logic.
- `from_env.py` — `VOXEnvConfig` + `load_env_config()`: `.env`/`os.environ`
  front-end (`environment overrides .env`). Raw env input; no precedence
  resolution against CLI.
- `resolver.py` — `resolve_config(cli_args, env_config)`:
  precedence **CLI > env > defaults**, type/required validation (e.g. mandatory
  `VOX_WAR_ROOM_ID`), and construction of the final `VOXConfig`.
- `loader.py` — `load_config()`: the public entry that composes the input
  sources and the resolver.

## Boundaries

### Producer → config
- The only external producer of raw input is the CLI entry (`src/vox/cli.py`),
  which calls `load_config()` and `load_cli_args()`. Raw source reads (env vars,
  argv) should normalize through `from_env`/`from_cli`, not be re-read ad hoc in
  application code.

### config → consumers
`VOXConfig` is the single resolved configuration object consumed across domains:

| Field | Consumer | Notes |
|---|---|---|
| `war_room_id` | orchestration `VOXOrchestrator` | End-to-end wired (env → resolve → consume). |
| `log_path` | `cli.py` logger setup | Consumed. |
| `uds_path` | runtime `control_plane.py` (server), `cli.py` (client) | Authoritative server path. |
| `verbose_logging` | **no consumer (NON-CONFORMANT)** | Resolved but never applied. See debt. |

Consumers must read resolved configuration from the `VOXConfig` they are given
(or from a config import of defaults) — never re-resolve the same setting from
its raw source, and never re-declare a duplicate default.

## Architectural invariants (target)

1. `VOXConfig` is the single authoritative process-configuration model; it is
   produced only by the config silo.
2. Defaults live in `models.py` with single ownership; no duplicated default
   literals anywhere in the codebase.
3. Precedence (`CLI > env > defaults`), parsing, type conversion, and required
   validation live in `resolver.py`/`from_*`; application code does not rederive
   precedence or re-read raw env sources for the same setting.
4. Raw sources normalize through `from_cli`/`from_env`; application code may
   consume resolved `VOXConfig` but must not open alternate resolution paths for
   the same setting.
5. Process config holds secret **references**, never resolved secret material.
6. Workload data and workload-store are fully packaged under the workload's
   private `instance/personas/<workload>/` directory; nothing about the workload
   data format imposes constraints on the config silo.

## Known boundary debt (current vs. target)

- **NON-CONFORMANT — UDS-path resolution split.** The UDS **client** path in
  `src/vox/cli.py` reads `VOX_UDS_PATH` directly (`from_env` is bypassed), while
  the server path resolves `VOXConfig.uds_path` to the hardcoded default (no env
  source). The two sides of one setting resolve differently, so a non-default
  `VOX_UDS_PATH` is honored by the client but not the server. Closing this fixes
  a behavior difference and belongs to `cli.py`/`from_env.py`; it is deferred
  because it changes server binding behavior.
- **Closed — `verbose_logging` is now consumed.** `VOXConfig.verbose_logging`
  (from `-v`/`VOX_VERBOSE_LOGGING`) is wired into `VOXForensicLogger` at the
  cli bootstrap construction site (`cli.py` passes `verbose=config.verbose_logging`).
  Behavior-preserving: default `False`.
- **Reported** — no env override currently exists for `log_path` (resolved to
  default only); this is a feature gap, not a boundary violation.
- **NON-CONFORMANT — `VOX_API_TOKEN` / `VOX_API_HOST` read directly in
  `src/vox/api_server.py`.** The HTTP-ingress auth token and bind host are read
  straight from `os.environ`, bypassing `from_env`/`VOXConfig`. These are raw
  env reads in application code, inconsistent with the "normalize through
  `from_env`" rule, and are **not** part of `VOXConfig`. Deferred as a
  cross-silo/config decision: promoting them into `VOXConfig` would change
  default/binding and authentication-enablement semantics. Recorded here as
  documented debt; the env reads are left in place.
- **NON-CONFORMANT — `VOX_WATCH_DISABLED` read directly in
  `src/vox/orchestration/base.py`.** This hot-reload feature toggle is read
  directly from `os.environ` in orchestration, bypassing the config silo. It is
  a raw env read in application code and is not part of `VOXConfig`. Deferred
  as a cross-silo/config decision: promoting it into `VOXConfig` (or explicit
  orchestration configuration) would change its resolution/default semantics.
  Recorded here as documented debt; the env read is left in place.

## Testing / architecture enforcement

- `pytest` (`python -m pytest`, asyncio auto mode). Behavior changes ship with
  tests in `tests/test_*.py`.
- `tests/test_config_arch.py` guards the silo: single default ownership (no
  duplicated UDS path literal outside `config/models.py`), no raw env re-read of
  a config-owned setting in application code, and config importing only its own
  submodules (no reverse packages).
- `tests/test_config.py` guards behavior (resolution precedence, env parsing,
  CLI parsing, load pipeline).

## What constitutes an architectural change

Any of the following must be proposed and reviewed as an architectural change:

- adding a field to `VOXConfig` or changing precedence/default semantics;
- moving resolution, parsing, or validation out of the config silo;
- adding a second resolution path for any setting already owned by `VOXConfig`;
- extending config to absorb workload/capability/secret configuration.