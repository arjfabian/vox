# AGENTS.md — observability silo

Normative working rules for the VOX observability layer in
`src/vox/observability/`. This is agent instruction, not project documentation.
It defines the **target architecture**: the boundary the observability layer
must converge on, not a record of every current internal. Existing code that
conflicts with the boundary is explicitly labeled NON-CONFORMANT and to be
closed, never treated as accepted architecture.

This contract is checked against the sibling AGENTS.md documents in
`capabilities`, `workloads`, `orchestration`, `runtime`, `config`, and
`security`; where those contracts reference the forensic logger or log
formatters, they are treated as established architecture.

## Responsibility

Observability is a **leaf service domain** (like `vox.security`): it owns the
logging/observability models, the forensic logger behavior, the output
formatters, and the observability constants. It imports nothing from the rest
of the codebase (stdlib + its own submodules only) and depends on no higher
silo.

Owned here:

- **Observability models** — `VOXForensicLogger` and `VOXLogSource`.
- **Forensic/event logging behavior** — the `ok/info/warning/error/debug`
  emission contract, source-metadata attachment (`extra={"vox_source": ...}`),
  and child-logger creation (`get_child`).
- **Verbosity/debug behavior** — the `verbose` flag on `VOXForensicLogger`
  gates `debug()` output, and is propagated through `get_child`.
- **Formatters / output representation** — `VOXColorFormatter`
  (human/color) and `VOXPlainFormatter` (machine/plain) and the `_source_tag`
  rule.
- **Observability constants** — `LOG_LEVEL_OK` in `constants.py` (level 25,
  registered name "OK").
- **Observability state and mutation** — state lives inside the formatters and
  the forensic logger; consumers do not reach formatter/logger internals.

## Modules

- `constants.py` — `LOG_LEVEL_OK` (the "OK" log level).
- `formatters.py` — `VOXColorFormatter`, `VOXPlainFormatter`, `_source_tag`.
- `models.py` — `VOXForensicLogger`, `VOXLogSource`.
- `__init__.py` — public package surface.

## The full observability flow

`configuration → logger construction → event emission → formatting → output`

1. **Configuration** (`vox.config`) owns the *intent*: `VOXConfig.log_path`,
   `VOXConfig.verbose_logging`. Resolved by `resolver`.
2. **Logger construction** is owned by the **bootstrap/entrypoint layer**
   (`src/vox/cli.py`), the single site that builds the root `"vox"` logger,
   attaches handlers/formatters, reads `config.log_path`, and wraps the result
   in `VOXForensicLogger(verbose=config.verbose_logging)`.
3. **Event emission** — consumers call `logger.info`/`ok`/`warning`/`error`/
   `debug`/`get_child` on a `VOXForensicLogger` they were handed. They do not
   reach into formatters or the raw `_logger`.
4. **Formatting** — the cli-installed `VOXColorFormatter`/`VOXPlainFormatter`
   on the `vox` hierarchy render each record.
5. **Output** — stream/`config.log_path` handlers.

Libraries use the standard `logging.getLogger(__name__)` child-of-`vox` loggers
for module-internal messages; these inherit the cli-installed handlers and
formatters, so they render through the same pipeline. The forensic wrapper adds
source metadata for explicit `VOXLogSource` records. Both paths converge on one
hierarchy — they are the standard Python logging pattern, not duplicated logger
ownership.

## Boundaries

### Out of the silo (imports/leaves)
Observability imports only stdlib (`logging`, `dataclasses`, `datetime`,
`typing`) and its own submodules. It must **never** import `vox.config`,
`vox.workloads`, `vox.orchestration`, `vox.runtime`, `vox.capabilities`,
`vox.api_server`, `vox.cli`, or `vox.security` — doing so would invert the
dependency direction.

### Consumers use the public surface
Consumers import the exported contracts (`VOXForensicLogger`, `VOXLogSource`,
`VOXColorFormatter`, `VOXPlainFormatter`, `LOG_LEVEL_OK`) and call the public
methods. They must not reach `VOXForensicLogger._logger`, the formatter color
tables, or other internals. `VOXLogSource.display_name`/`short_uuid` are the
public read surface.

### Public surface
`__init__.__all__` must include the constants and objects consumed across
silos. A deep-path import (`vox.observability.constants.LOG_LEVEL_OK`) is a
signal a consumed symbol is missing from the package root — export it rather
than encouraging deep imports.

## Architectural invariants (target)

1. The observability silo imports only stdlib and its own submodules.
2. `cli.py` is the single logger-construction site; it reads log configuration
   from `VOXConfig` and wires it into the formatters/handlers and the
   `VOXForensicLogger` (including `verbose`).
3. `VOXForensicLogger.verbose` gates `debug()`; it is passed at construction and
   propagated by `get_child`. Verbosity is a resolved-config concern, owned by
   config for the intent and by bootstrap for the wiring.
4. Consumers never reach `VOXForensicLogger._logger`, formatter internals, or
   the underlying `logging` state set up during construction.
5. The consumed `LOG_LEVEL_OK` and all other cross-silo symbols are exported
   from the package root.
6. Observability owns no workload/fleet/common topology configuration, no
   capability contracts, and no security/vault semantics.

## What constitutes an architectural change

Any of the following must be proposed and reviewed as an architectural change,
not a routine edit:

- adding/changing the public exports or a model/formatter public method
  signature;
- changing the forensic logger's emission contract (levels, `extra`,
  `get_child` semantics) or the `verbose` gating rule;
- changing the log-level constant or output representation (formatters);
- relocating logger construction away from the single bootstrap site, or adding
  a second construction/formatter site.

## Testing / architecture enforcement

- `pytest` (`python -m pytest`, asyncio auto mode). Behavior changes ship with
  tests in `tests/test_*.py`.
- `tests/test_observability_arch.py` guards the silo: no out-of-silo imports,
  the public-surface completeness (deep-imported constants are exported), the
  single logger-construction site, and the verbose wiring at construction.
- `tests/test_observability.py` guards formatter/logger/source behavior.

## Known boundary debt (current vs. target)

- **Closed — verbose logging now wired.** `VOXConfig.verbose_logging` was
  resolved by `vox.config` but never applied, leaving every `logger.debug(...)`
  dead. The observability `verbose` behavior was already correct; the missing
  piece was the bootstrap wiring. `cli.py` now constructs
  `VOXForensicLogger(base_logger, verbose=config.verbose_logging)`, activating
  the resolved intent only when `-v`/`VOX_VERBOSE_LOGGING` is set (default
  `False`, behavior-preserving).
- **Closed (export) — `LOG_LEVEL_OK` is now public.** It was consumed only via a
  deep import (`vox.observability.constants.LOG_LEVEL_OK` from the comm.gateway
  server) because it was missing from the package root; it is now exported from
  `vox.observability`. **Deferred (consumer migration)** — `comm/gateway/server.py`
  still deep-imports `vox.observability.constants.LOG_LEVEL_OK` instead of the
  package public surface; that consumer-side migration remains NON-CONFORMANT
  and is allowed as documented debt until moved to the public root.
- **Note (not a violation)** — library modules (capabilities, workloads
  memory/store) keep module-level `logging.getLogger(__name__)` loggers on the
  same `vox` hierarchy as the forensic wrapper. This is the standard Python
  logging pattern and both render through the single cli-installed
  construction; it is not duplicated logger ownership.