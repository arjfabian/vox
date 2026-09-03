# AGENTS.md — capabilities silo

Normative working rules for the VOX capability subsystem. This is agent
instruction, not project documentation. It is grounded in the current
implementation in `src/vox/capabilities/` and the host boundary fixed by
`CapabilityHostProtocol` (see `src/vox/provider.py`).

## Base model

A capability is split into two classes in `src/vox/capabilities/base.py`:

- `VOXCapability` — stateless, shared definition. Identified by
  `CAPABILITY_NAME = "ns.name"`. Holds no per-workload state. All configuration
  metadata comes from `capability.yml`; there is no legacy Python-side contract.
- `VOXBoundCapability` — the per-workload runtime proxy. Holds the resolved
  (non-secret) `params` and the injected `secrets`, and delegates all runtime
  services to the `CapabilityHostProtocol`. It never reaches into a concrete
  workload.

A capability directory requires:
- `capability.py` — implementation (a `VOXCapability` subclass).
- `capability.yml` — the authoritative contract. Additional modules such as
  `models.py`/`client.py` are optional.

## Lifecycle

- `@classmethod async health_check() -> bool` — off-line readiness probe.
- `async boot() -> None` — allocate and start runtime resources (clients,
  servers, caches). Runs once per bound capability per workload.
- `async shutdown() -> None` — release what `boot()` created.
- `mount(host, overrides)` (framework) — builds a `VOXBoundCapability` from
  YAML params plus manifest overrides. Only parameters declared under `params`
  may be overridden; unknown overrides raise. Secrets are never accepted here.

## `CapabilityHostProtocol` — the host boundary

`CapabilityHostProtocol` is the **only** coupling surface between a capability
and the workload runtime. `VOXBoundCapability` and `CapabilityBinder` depend on
it, never on `VOXWorkload`, the `VOXOrchestrator`, or any provider internals.

Capability-facing services (legal for a capability to consume):

- `get_safe_path(sub_dir, filename)` — resolve a capability-owned resource to a
  path the runtime guarantees is safe.
- `get_capability(cap_id)` — cross-capability lookup through the host registry.
- `dispatch_inbound(source, payload) -> bool` — narrow inbound-dispatch service;
  returns `True` when a target actually received the message, `False` when there
  is no provider, the guardrail rejected it, or no matching workload is mounted.
  A capability never reaches for the whole provider abstraction.
- `register_capability_command(name, handler)` — register a YAML-declared
  exposed command.

The bound proxy also exposes `log/warning/error/ok` and
`get_secret_names()/get_required_secret_names()/get_exposed_commands()` derived
from the contract.

The remaining host members (`name`, `id`, `dir`, `config`, `vault`,
`capability_provider`, `capabilities`, `register_capability`,
`mark_degraded`, `disable_roles`) are binder/composition-facing. A capability
must not require them as part of its context.

## Connectors (current implementations)

- `comm.email` — SMTP dispatch. `send_email(to, subject, body)`. SMTP host/port
  are `params`; `SMTP_USER`/`SMTP_PASS` are Vault `secrets`. `send_email` is a
  YAML-declared exposed command.
- `comm.gateway` — multi-channel inbound/outbound messaging. Shared
  `IngressServer` with reference-counted lifecycle; inbound is dispatched via
  `host.dispatch_inbound`. Outbound via `send_message(VOXOutboundMessage)` /
  `send_text(channel, recipient_id, text)`. Channels are registered in
  `adapters/__init__.py` `ADAPTER_REGISTRY` as `BaseAdapter` subclasses
  (`telegram`, `whatsapp`, `webhook`); each adapter's config lives in its own
  `config.yml`, aggregated into the gateway `CapabilityContract`. No
  channel-specific code lives in `capability.py`/`server.py`.
- `comm.voicetotext` — offline transcription via `transcribe(file_path)`.
  `WHISPER_*` are `params`.
- `ai.llm` — unified generation via `generate(...)`, `chat(...)`,
  `generate_vision(...)`, `parse_intent(...)`. `LLM_*` are `params` (opaque
  backend URL/model/etc.). None are secrets.
- `net.browser` — headless browsing: `capture_page(url)` /
  `get_text(url)`. `BROWSER_*` are `params`; captured assets are written via
  `get_safe_path("evidence", ...)`.

## `capability.yml` — authoritative contract

`capability.yml` is the single source of truth for configuration metadata:

- `params` — non-secret configuration (name, type, description, default).
- `secrets` — Vault-managed values (name, type, description, `required` bool,
  default `true`). Never a `param`.
- `provides` and `exposed_commands` — identity and command declarations.

Rules:

- `params` and `secrets` are disjoint namespaces. A name cannot be both.
- Exposed commands are declared in YAML, never as Python class attributes
  (`EXPOSED_COMMANDS` is forbidden).
- Secret metadata lives only in the `secrets` namespace, never as parameters.
- Capability code reads configuration only through attributes declared in its
  contract (aggregated for `comm.gateway` from adapter `config.yml` files).

## Resource / dependency rules

- Capability-owned resources are resolved via `get_safe_path`, with no
  process-working-directory assumptions imposed on the capability.
- Optional credentials MUST be declared with default `""` (never `None`).
  In bound params, `None` means "required — workload flagged DEGRADED if
  missing"; `""` means "unconfigured / feature inactive".
- Cross-capability access goes through `host.get_capability`, never a concrete
  registry.

## Forbidden access

A capability must not:

- reference `_workload`, `_capability_provider`, `_degraded`,
  `_capability_commands`, or `.orchestrator`;
- import `VOXWorkload`, `VOXOrchestrator`, or orchestrator internals;
- reach the whole `CapabilityProviderProtocol` from capability code (use the
  narrow `host.dispatch_inbound` for inbound);
- call `send_broadcast()` (outbound is `send_text`, with explicit channel and
  recipient);
- place credentials/tokens in source, tests, logs, or commits (Vault only).

## Testing / architecture enforcement

- `pytest` (`python -m pytest`) in asyncio auto mode; `tests/test_capability_arch.py`
  enforces the silo invariants: param/secret separation, YAML-authoritative
  contracts, identity, no private/provider access in capability/binder source,
  narrow inbound-dispatch boundary, and the contract-usage guard (capability
  code reads only declared contract attributes, gateway aggregated).
- Behavior changes ship with tests in `tests/test_*.py`.
- New focused architecture tests belong in `tests/test_capability_arch.py`.

## What requires an architectural change

Any of the following is an architecture change, not a routine edit — it must be
proposed as such (and thereby re-examined against `CapabilityHostProtocol`):

- widening the capability-facing subset of `CapabilityHostProtocol`;
- adding a new capability, channel/adapter, or connector;
- changing inbound-dispatch routing or the `CapabilityProviderProtocol`
  signature;
- introducing a capability-side resource that is not resolvable through
  `get_safe_path`.
