# AGENTS.md — capabilities silo

Normative working rules for the VOX capability subsystem. This is agent
instruction, not project documentation. It is grounded in the current
implementation in `src/vox/capabilities/` and the host boundary fixed by
`CapabilityHostProtocol` (see `src/vox/provider.py`).

## Base model

A capability is split into two classes in `src/vox/capabilities/base.py`:

* `VOXCapability` — stateless, shared definition. Identified by
  `CAPABILITY_NAME = "ns.name"`. Holds no per-workload state. All configuration
  metadata comes from `capability.yml`; there is no legacy Python-side contract.
* `VOXBoundCapability` — the per-workload runtime proxy. Holds the resolved
  (non-secret) `params` and the injected `secrets`, and delegates all runtime
  services to the `CapabilityHostProtocol`. It never reaches into a concrete
  workload.

A capability directory requires:

* `capability.py` — implementation (a `VOXCapability` subclass).
* `capability.yml` — the authoritative contract.
* Additional modules such as `models.py`, `client.py`, or `adapters/` are
  optional and must remain within the capability boundary.

## Lifecycle

* `@classmethod async health_check() -> bool` — off-line readiness probe.
* `async boot() -> None` — allocate and start runtime resources (clients,
  servers, caches). Runs once per bound capability per workload.
* `async shutdown() -> None` — release what `boot()` created.
* `mount(host, overrides)` (framework) — builds a `VOXBoundCapability` from
  YAML params plus manifest overrides. Only parameters declared under `params`
  may be overridden; unknown overrides raise. Secrets are never accepted here.

## Adapters

An adapter is a concrete implementation of an external protocol, backend,
transport, or provider behind a capability port.

Adapters are part of the capability boundary. They are not independent
capabilities and must not bypass the capability contract or the
`CapabilityHostProtocol`.

The canonical VOX adapter architecture is established by `comm.gateway` and
MUST be reused by other capability ports such as `ai.llm`.

### Port vs. adapter

A capability port defines the provider-agnostic interface consumed by Roles and
other capabilities.

An adapter implements that interface for one concrete backend.

For example:

```text
consumer
    |
    v
ai.llm                         <- provider-agnostic port
    |
    +-- OllamaAdapter           <- concrete backend adapter
    |
    +-- GeminiAdapter           <- concrete backend adapter
```

and:

```text
consumer
    |
    v
comm.gateway                   <- channel-agnostic port
    |
    +-- TelegramAdapter         <- concrete channel adapter
    |
    +-- WhatsAppAdapter
    |
    +-- WebhookAdapter
```

The port MUST NOT contain provider-specific implementation details.

The port MUST NOT silently select a concrete backend.

The consumer MUST NOT import or instantiate a concrete adapter directly.

### Adapter availability vs. selection

Adapter **provisioning** and adapter **selection** are distinct concerns.

Provisioning decides which adapters are *available and authorized* for a
workload. It belongs to the workload/capability composition boundary and may be
expressed in composition or configuration metadata. Provisioning must not be
read as a lifetime binding: it does not imply that one adapter is selected for
the workload for its entire lifetime.

Selection decides which adapter a *particular operation* uses. Selection is an
execution-time concern:

* A capability port exposes provider-agnostic operations.
* When an operation requires a concrete adapter, the adapter identity must be
  explicit in the operation/request.
* Different operations may select different adapters.
* Adapter availability MUST NOT equal adapter selection.
* `is_configured()` and readiness/health checks describe availability and
  readiness, not runtime selection.
* Adapter identity and provider/model configuration are separate concerns. For
  LLMs in particular, selecting the adapter (e.g. Ollama vs Gemini) and
  selecting the model (a specific Ollama/Gemini model) are separate dimensions.

`comm.gateway` is the established precedent: a workload may have several
communication adapters available, and each outbound operation selects the
destination channel explicitly at execution time.

A capability may have zero, one, or multiple adapters; one adapter per
capability or per workload is not the model. A manifest-level declaration may
scope which adapters are available to a workload, but it does not fix the
adapter any single operation uses.

Adapter selection must not be inferred from arbitrary runtime state,
environment variables, model names, URLs, or fallback heuristics. A capability
port may resolve a concrete backend according to the established capability
framework, but the resolution must be driven by the explicit adapter identity
carried by the operation/request.

Do not introduce a generic provider-selection abstraction merely to avoid
explicit adapter selection.

When multiple adapters are available for the same port, their identities must
be unambiguous.

A consumer may request a capability port and use its stable interface, but must
remain unaware of provider-specific implementation details.

For example:

```text
image.ocr
    -> ai.llm
       -> GeminiAdapter
          -> Gemini API
```

is valid.

This is NOT valid:

```text
image.ocr
    -> GeminiAdapter
       -> Gemini API
```

Likewise, a Role must not import `OllamaAdapter`, `GeminiAdapter`,
`TelegramAdapter`, or equivalent concrete implementations.

### Adapter implementation rules

Every adapter MUST:

* implement the contract of its parent capability port;
* remain provider-specific internally;
* keep provider-specific protocol, endpoint, SDK, model, and authentication
  details inside the adapter;
* use the existing capability lifecycle (`health_check`, `boot`, `shutdown`)
  where lifecycle management is required;
* obtain configuration through the capability contract and existing capability
  parameter mechanisms;
* obtain credentials through the existing Vault secret-injection mechanism;
* avoid introducing a parallel configuration, credential, registry, or lifecycle
  system;
* expose no provider-specific API to Roles or unrelated capabilities.

An adapter MUST NOT:

* become a second capability port;
* bypass `VOXBoundCapability`;
* access `VOXWorkload` or orchestrator internals;
* access Vault implementation internals directly;
* read secrets directly from arbitrary environment variables;
* hard-code credentials;
* make provider-specific behavior part of the port contract;
* silently fall back to another adapter/provider unless the port contract
  explicitly defines such behavior.

### Adapter configuration

Adapter-specific configuration belongs to the adapter's own `config.yml`.

Configuration MUST follow the normal capability contract rules:

* non-secret adapter configuration belongs under `params`;
* credentials/tokens belong under `secrets`;
* secrets are injected through the normal workload Vault path;
* adapter-specific names MUST NOT leak into the provider-agnostic port contract
  unless they are genuinely part of the port interface.

For a capability with multiple adapters, adapter configuration may be aggregated
into the parent capability contract using the same mechanism established by
`comm.gateway`.

Do not create a second configuration format for adapters.

### Adapter registration

Adapter discovery/registration MUST follow the established capability mechanism.

If a capability maintains an adapter registry, the registry must contain only
adapter implementations of that capability's port and must use stable adapter
identifiers.

The registry is composition metadata; it is not a substitute for the capability
port.

Adding a new adapter is an architectural change and requires:

1. an adapter implementation;
2. adapter-local configuration metadata;
3. explicit registration/discovery;
4. capability architecture tests;
5. behavioral tests for the adapter;
6. verification that provider-specific dependencies do not leak outside the
   adapter.

Use `comm.gateway` as the reference implementation for adapter lifecycle,
registration, configuration aggregation, and execution-time selection. Do not
infer a different adapter architecture from an individual provider
implementation.

### Multiple adapters

A capability port may have zero, one, or multiple adapters available to a
workload.

The architecture MUST support multiple adapters without requiring consumers to
know how they are implemented.

Availability of adapters may be scoped per workload through the
composition/configuration layer; the adapter actually used by a given operation
is decided at execution time. Different consumers may use the same port with
different adapters, and a single consumer may use several adapters
interchangeably across operations.

For example, it is valid for a workload to use:

```text
ChatRole
    -> ai.llm
       -> OllamaAdapter
          -> llama3.2:1b

image.ocr
    -> ai.llm
       -> GeminiAdapter
          -> gemini-2.5-flash
```

The fact that both consumers use `ai.llm` does not imply that they must use the
same concrete backend. Neither does a workload's composition imply a single
adapter binding for the lifetime of the workload.

Provider-specific model selection belongs to the selected adapter, not to the
generic port. Adapter selection (which backend) and model selection (which
provider configuration) remain separate dimensions.

### Adapter dependencies

An adapter may depend on other capabilities only through the same
`CapabilityHostProtocol` rules as any other capability implementation.

An adapter MUST NOT directly import or instantiate another capability's
implementation merely because that implementation is convenient.

The capability dependency graph, including dependencies originating inside
adapters, MUST remain acyclic.

### Adapter reference implementation

`comm.gateway` is the canonical implementation of the VOX adapter pattern.

When implementing or refactoring another capability to use adapters:

1. read this section first;
2. inspect `comm.gateway` for the concrete framework mechanics;
3. reproduce the established adapter lifecycle, registration, configuration,
   and availability-vs-selection pattern;
4. do not copy channel-specific semantics into the new capability;
5. do not invent a second adapter mechanism.

The goal is one adapter architecture across VOX, not one adapter architecture
per capability.

## `CapabilityHostProtocol` — the host boundary

`CapabilityHostProtocol` is the **only** coupling surface between a capability
and the workload runtime. `VOXBoundCapability` and `CapabilityBinder` depend on
it, never on `VOXWorkload`, the `VOXOrchestrator`, or any provider internals.

Capability-facing services (legal for a capability to consume):

* `get_safe_path(sub_dir, filename)` — resolve a capability-owned resource to a
  path the runtime guarantees is safe.
* `get_capability(cap_id)` — cross-capability lookup through the host registry.
* `dispatch_inbound(source, payload) -> bool` — narrow inbound-dispatch service;
  returns `True` when a target actually received the message, `False` when there
  is no provider, the guardrail rejected it, or no matching workload is mounted.
  A capability never reaches for the whole provider abstraction.
* `register_capability_command(name, handler)` — register a YAML-declared
  exposed command.

The bound proxy also exposes `log/warning/error/ok` and
`get_secret_names()/get_required_secret_names()/get_exposed_commands()` derived
from the contract.

The remaining host members (`name`, `id`, `dir`, `config`, `vault`,
`capability_provider`, `capabilities`, `register_capability`,
`mark_degraded`, `disable_roles`) are binder/composition-facing. A capability
must not require them as part of its context.

## Connectors and ports (current implementations)

* `comm.email` — SMTP dispatch. `send_email(to, subject, body)`. SMTP host/port
  are `params`; `SMTP_USER`/`SMTP_PASS` are Vault `secrets`. `send_email` is a
  YAML-declared exposed command.
* `comm.gateway` — channel-agnostic inbound/outbound messaging port. A single
  process-level `IngressServer` is shared across workloads and owns the inbound
  webhook routes plus a bound-user reference count; it shuts down when the final
  bound user detaches. Each bound capability (workload) creates and owns its own
  configured adapter instances from its own Vault secrets and manifest params —
  adapter instances and credentials are never shared between workloads. Inbound
  is dispatched via the route-owner's `host.dispatch_inbound`. Outbound via
  `send_message(VOXOutboundMessage)` / `send_text(channel, recipient_id, text)`;
  the channel is selected per operation from the workload's own adapter set.
  Channels are implemented as adapters registered in `adapters/__init__.py`
  `ADAPTER_REGISTRY` as `BaseAdapter` subclasses (`telegram`, `whatsapp`,
  `webhook`). Each adapter's configuration lives in its own `config.yml` and is
  aggregated into the gateway `CapabilityContract`. Channel availability is
  decided at mount time via each adapter's `is_configured()`. Adapter lifecycle
  (`start`/`shutdown`) is part of the `BaseAdapter` contract: only the
  route-owning adapter instance performs inbound activity; every adapter
  instance is shut down with its own workload. A channel's inbound webhook path
  has a single owner within the process. No channel-specific code lives in
  `capability.py`/`server.py`. `comm.gateway` is the canonical reference
  implementation for the VOX adapter pattern.
* `comm.voicetotext` — offline transcription via `transcribe(file_path)`.
  `WHISPER_*` are `params`.
* `ai.llm` — provider-agnostic LLM inference port via `generate(...)`,
  `chat(...)`, `generate_vision(...)`, and `parse_intent(...)`. Concrete
  backends are `LLMAdapter` subclasses registered in `adapters/__init__.py`
  `ADAPTER_REGISTRY` under stable adapter ids (`ollama`, `gemini`). Each
  adapter owns a `config.yml` (params, plus Vault secrets such as
  `GEMINI_API_KEY`) aggregated into the port's `CapabilityContract` by
  `load_contract()` — the `comm.gateway` mechanism; no generic `LLM_API_KEY`
  exists. Each bound capability (workload) builds and owns its own adapter
  instances from its own params and injected Vault secrets; instances and
  credentials are never shared between workloads. Adapter availability is
  decided at boot via each adapter's `is_configured()`; the adapter used by an
  operation is selected explicitly via the required keyword-only `adapter=`
  argument (`generate`/`chat`/`generate_vision`/`parse_intent`), with an
  optional per-operation `model=` override. `start`/`shutdown` are part of the
  `LLMAdapter` contract and run with the bound capability's lifecycle. The
  semantic-cache key includes both adapter identity and model. Provider
  protocol, endpoint, model defaults, and credential handling live only inside
  the adapters — `capability.py` contains no provider logic and performs no
  selection. Consumers such as `ai.parsing` and `image.ocr` use the port and
  must not import concrete LLM adapters.
* `ai.parsing` — natural-language command/intent resolution. `parse(text,
  vocabulary) -> ParsedIntent` where `vocabulary` is a list of `CommandSpec`
  (name + description); returns a normalized intent (command, confidence,
  entities). `ai.parsing` owns intent semantics; it consumes `ai.llm` through
  `host.get_capability("ai.llm")`, never importing a concrete LLM provider or
  adapter. It is decoupled from any channel, role, workload, or fleet topology.
  An unresolved input returns an empty-string `command` (a caller-mapped
  default, e.g. conversational fallback). The `parse` entry point is the stable
  extension point for a future cosine/semantic resolver fallback.
* `net.browser` — headless browsing: `capture_page(url)` / `get_text(url)`.
  `BROWSER_*` are `params`; captured assets are written via
  `get_safe_path("evidence", ...)`.
* `image.ocr` — image-to-text extraction through the provider-agnostic
  `ai.llm` port. OCR semantics belong to `image.ocr`; provider-specific vision
  behavior belongs to the selected `ai.llm` adapter. It must not import a
  concrete LLM provider or adapter.

## `capability.yml` — authoritative contract

`capability.yml` is the single source of truth for configuration metadata:

* `params` — non-secret configuration (name, type, description, default).
* `secrets` — Vault-managed values (name, type, description, `required` bool,
  default `true`). Never a `param`.
* `provides` and `exposed_commands` — identity and command declarations.

Rules:

* `params` and `secrets` are disjoint namespaces. A name cannot be both.
* Exposed commands are declared in YAML, never as Python class attributes
  (`EXPOSED_COMMANDS` is forbidden).
* Secret metadata lives only in the `secrets` namespace, never as parameters.
* Capability code reads configuration only through attributes declared in its
  contract.
* For adapter-based capabilities, adapter configuration may be aggregated from
  adapter-local `config.yml` files according to the established `comm.gateway`
  mechanism.

## Resource / dependency rules

* Capability-owned resources are resolved via `get_safe_path`, with no
  process-working-directory assumptions imposed on the capability.
* Optional credentials MUST be declared with default `""` (never `None`). In
  bound params, `None` means "required — workload flagged DEGRADED if missing";
  `""` means "unconfigured / feature inactive".
* Cross-capability access MUST go through `host.get_capability`, never a
  concrete registry or direct capability import.
* The capability dependency graph MUST remain acyclic. A capability must never
  introduce a dependency path that eventually resolves back to itself.
* Provider-specific dependencies MUST remain inside their adapter. A provider
  SDK, provider-specific client, or provider-specific protocol implementation
  must not leak into the port or its consumers.

## Forbidden access

A capability must not:

* reference `_workload`, `_capability_provider`, `_degraded`,
  `_capability_commands`, or `.orchestrator`;
* import `VOXWorkload`, `VOXOrchestrator`, or orchestrator internals;
* reach the whole `CapabilityProviderProtocol` from capability code (use the
  narrow `host.dispatch_inbound` for inbound);
* call `send_broadcast()` (outbound is `send_text`, with explicit channel and
  recipient);
* place credentials/tokens in source, tests, logs, or commits (Vault only);
* import a concrete adapter from another capability or from a Role;
* instantiate an adapter directly from a Role or unrelated capability;
* bypass the parent capability port to access an external provider;
* introduce provider-specific selection heuristics or implicit fallback.

## Testing / architecture enforcement

* `pytest` (`python -m pytest`) in asyncio auto mode;
  `tests/test_capability_arch.py` enforces the silo invariants: param/secret
  separation, YAML-authoritative contracts, identity, no private/provider
  access in capability/binder source, narrow inbound-dispatch boundary, and
  the contract-usage guard (capability code reads only declared contract
  attributes, gateway aggregated).
* Adapter-based capabilities MUST test:

  * adapter registration/discovery;
  * execution-time adapter selection;
  * adapter-specific configuration;
  * adapter-specific secret declaration and injection;
  * provider isolation;
  * the port contract independently from concrete providers.
* Behavior changes ship with tests in `tests/test_*.py`.
* New focused architecture tests belong in `tests/test_capability_arch.py`.
* Tests must not contain real credentials or provider tokens. Use mocks,
  fixtures, or test credentials that cannot grant access to real resources.

## What requires an architectural change

Any of the following is an architecture change, not a routine edit — it must be
proposed as such (and thereby re-examined against `CapabilityHostProtocol`):

* widening the capability-facing subset of `CapabilityHostProtocol`;
* adding a new capability, channel/adapter, or connector;
* changing inbound-dispatch routing or the `CapabilityProviderProtocol`
  signature;
* introducing a capability-side resource that is not resolvable through
  `get_safe_path`;
* changing the port/adapter boundary or introducing a new adapter-selection
  mechanism;
* allowing provider-specific implementation details to cross the port boundary;
* changing the adapter registration, lifecycle, configuration aggregation, or
  credential-injection mechanism.

When an architectural change is required, do not silently implement the new
pattern as a local exception. Stop, describe the proposed boundary, and update
the relevant architecture rules and tests together.
