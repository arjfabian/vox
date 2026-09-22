# AGENTS.md

Working rules for coding agents in the VOX repository. VOX is a pre-1.0 Python
multi-agent orchestration framework, package in `src/` layout.
This file is instructions for agents, not project documentation — see
README.md, CHANGELOG.md, and docs/internal/ for context.

## Layout

- `src/vox/` — the Python package. Application/runtime modules belong here;
  operational scripts belong under `tools/`.
- `src/vox/capabilities/` — one directory per capability (`<ns>.<name>`).
  Each capability directory requires `capability.py` and `capability.yml`;
  additional modules such as `models.py` are optional.
- `src/vox/capabilities/comm/gateway/` — reference architecture: core
  (`capability.py`, `server.py`) is channel-agnostic; channels are `BaseAdapter`
  subclasses under `adapters/`.
- `workloads/` — runtime workload definitions (`workload.yml`); not code.
- `tools/` — one-off operational scripts.
- `tests/` — pytest suite; `tests/fixtures/` for captured payload fixtures.
- `docs/internal/` — architecture rationale and design documents.

## Architecture invariants

- **Capability pattern.** A capability is a `VOXCapability` subclass plus a
  `capability.yml` (`name`, `version`, `description`, `provides`, `params`,
  `secrets`). YAML is the **single source of truth** for parameter and secret
  metadata. Python classes implement behavior only — never redeclare config
  metadata via `PARAMS` or `SENSITIVE_PARAMS`.
- **CapabilityContract.** Loaded from YAML; holds `params` (`dict[str, ParamMeta]`)
  and `secrets` (`dict[str, SecretMeta]`). `SecretMeta.required` (bool,
  default `True`) marks Vault secrets that must be present. Use
  `CapabilityContract.required_secret_names` to query required secrets.
- **Config sentinels.** In bound capability params, `None` means "required —
  workload flagged DEGRADED if missing". Empty string `""` means "unconfigured /
  feature inactive". Optional credentials MUST default to `""`, never `None`.
- **comm.gateway channels.** Adapter config is defined in per-adapter
  `config.yml` files (under `adapters/<channel>/config.yml`), NOT in Python
  class attributes. The gateway's `load_contract()` aggregates adapter
  configs into a single `CapabilityContract`. A new channel is a
  `BaseAdapter` subclass with `CHANNEL`, `WEBHOOK_PATH`, `is_configured`,
  registered in `adapters/__init__.py` `ADAPTER_REGISTRY`.
  Do not add channel-specific code to `capability.py` or `server.py`.
- **ai.llm adapters.** Same pattern as `comm.gateway`. The port
  (`generate`/`chat`/`generate_vision`/`parse_intent`) is provider-agnostic;
  each backend is an `LLMAdapter` subclass under
  `ai/llm/adapters/<adapter>/` with its own `config.yml` (e.g. `ollama`,
  `gemini` + Vault secret `GEMINI_API_KEY`), aggregated into the port
  contract by `load_contract()`. The adapter used by an operation is selected
  explicitly via the required keyword-only `adapter=` argument; `model=` is an
  optional per-operation override. Do not add provider-specific code or
  selectors to `capability.py`, and never import a concrete LLM adapter from a
  consumer (e.g. `ai.parsing`, `image.ocr`).
- **Body-signing.** `verify_request(request, body)` verifies against the raw
  request bytes read before JSON parsing, never against `request.json()`.
- **Inbound.** Dispatch through `orchestrator.dispatch_inbound_message(
  source=..., payload=...)`, wrapped to catch `SecurityError` (guardrail).
- **Outbound.** No `send_broadcast()`. Call
  `send_text(channel, recipient_id, text, ...)` with explicit channel and
  recipient.
- **Secrets.** Injected via Vault through the `secrets` section of YAML
  contracts. Roles may declare `REQUIRED_SECRETS = {"cap_id": ["SECRET", ...]}`
  to request Vault secrets per capability; the binder intersects these with the
  YAML contract's `required:true` flags to determine which secrets must be
  present. Never put credentials or tokens in source, tests, logs, or commits.
- **AST scanning.** `ASTWorkloadAnalyzer` statically scans role files for
  capability usage (e.g. `self.workload.capabilities["cap_id"]`) and
  `REQUIRED_SECRETS` (per-cap secret names) without executing code.

These invariants are protected by tests in the suite; this file only tells you
they exist. Prose is not an enforcement mechanism.

## Style

- `from __future__ import annotations`, modern type annotations, module
  docstrings, `# ---` section dividers in larger modules.
- `# noqa:` comments carry a rule code (e.g. `# noqa: RUF012`).

## Tests

- Run `python -m pytest` (config in pyproject.toml; asyncio auto mode — no
  manual event-loop plumbing, no second runner).
- Behavior changes ship with tests in `tests/test_*.py`.
- Stub/helper classes in test files start with `_` so pytest does not collect
  them.
- Outbound HTTP is mocked with `httpx.MockTransport`.
- Captured external payloads live in `tests/fixtures/` with real identifiers
  replaced by synthetic placeholders; the guard test
  `test_fixtures_contain_no_real_captured_identifiers` enforces this.

## Commits

- Conventional Commits: `type(scope): subject` (feat, fix, refactor, test,
  chore, docs, arch, release). Scope is a module (e.g. `comm.gateway`).
- Do not commit unless explicitly asked.
- One logical change per commit.
- When explicitly preparing a release, bump `version` in pyproject.toml, add a
  dated `## [x.y.z]` entry to CHANGELOG.md, then tag `vX.Y.Z`. Detail goes in
  CHANGELOG, not README.

## Changelog

Use these sections in `CHANGELOG.md` consistently:

- **Added** — new user-visible capabilities or functionality.
- **Changed** — changes to existing behavior, interfaces, contracts, or
  architecture that are not bug fixes and are not purely internal
  restructuring.
- **Fixed** — bugs, incorrect behavior, regressions, or security issues that
  were corrected.
- **Refactored** — significant internal restructuring or architectural
  reorganization that is not itself a new feature or a bug fix.
- **Removed** — APIs, components, behaviors, or capabilities that were removed.
- **Documentation** — documentation-only changes that do not change product
  behavior, architecture, or security.
- **Security** — security changes significant enough to warrant dedicated
  visibility. Use this section for security hardening, security-boundary
  changes, credential-handling changes, or other security-relevant changes.

A changelog entry may use multiple sections when appropriate. Choose the
section based on the nature of the change, not on the file that was modified.
For example, an architectural refactor belongs under `Refactored` even if it
changes many Python files, while a documentation-only reorganization belongs
under `Documentation` even if it modifies `CHANGELOG.md`.

Do not duplicate the same change across sections unless the distinction is
necessary to communicate separate user-visible effects.

When reorganizing an existing changelog, preserve the entry wording and
technical content unless the task explicitly requests editorial changes.

## Done checklist

- `python -m pytest` passes.
- If a lint tool is available in the environment (e.g. ruff), run it and
  address findings.
- No new secrets in code, tests, or commits.
