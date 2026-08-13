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
- `agents/` — runtime agent definitions (`agent.yml`); not code.
- `tools/` — one-off operational scripts.
- `tests/` — pytest suite; `tests/fixtures/` for captured payload fixtures.
- `docs/internal/` — architecture rationale and design documents.

## Architecture invariants

- **Capability pattern.** A capability is a `VOXCapability` subclass plus a
  `capability.yml` (`name`, `capability_class`, `provides`, `params`).
  Declare params as `PARAMS = {"ENV_VAR": ["description", default]}` and
  secrets in `SENSITIVE_PARAMS`. Mutable class-level `PARAMS` dicts are an
  intentional pattern — keep `# noqa: RUF012`.
- **Config sentinels.** In bound capability params, `None` means "required —
  agent flagged DEGRADED if missing". Empty string `""` means "unconfigured /
  feature inactive". Optional credentials MUST default to `""`, never `None`.
- **comm.gateway channels.** A new channel is a `BaseAdapter` subclass declaring
  `CHANNEL`, `WEBHOOK_PATH`, `PARAMS`, `SENSITIVE_PARAMS`, `is_configured`,
  registered in `adapters/__init__.py` `ADAPTER_REGISTRY`.
  Do not add channel-specific code to `capability.py` or `server.py`.
- **Body-signing.** `verify_request(request, body)` verifies against the raw
  request bytes read before JSON parsing, never against `request.json()`.
- **Inbound.** Dispatch through `orchestrator.dispatch_inbound_message(
  source=..., payload=...)`, wrapped to catch `SecurityError` (guardrail).
- **Outbound.** No `send_broadcast()`. Call
  `send_text(channel, recipient_id, text, ...)` with explicit channel and
  recipient.
- **Secrets.** Injected via vault through `SENSITIVE_PARAMS`. Never put
  credentials or tokens in source, tests, logs, or commits.

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

## Done checklist

- `python -m pytest` passes.
- If a lint tool is available in the environment (e.g. ruff), run it and
  address findings.
- No new secrets in code, tests, or commits.
