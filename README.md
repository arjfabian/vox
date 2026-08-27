# VOX

VOX is an identity-first orchestration platform for autonomous workloads. It
manages long-lived execution units with persistent identity, explicit resource
grants, and independent audit storage.

VOX separates reasoning from authority. AI models may produce decisions, but
the platform controls what those decisions can act upon. Capabilities,
filesystem access, communication paths and execution permissions are governed
by the orchestrator, not by the workload itself. This is the central
architectural invariant: **orchestration owns authority; workloads never own
authority.**

In VOX, reasoning is advisory; authority is architectural.

## Why VOX exists

Autonomous workloads can generate an unbounded sequence of decisions. If the
workload carries ambient authority, every decision inherits it, and the only
defence is the model's behaviour. Relying on model behaviour does not produce
predictable security guarantees.

VOX externalises authority from the workload. The orchestrator grants access
to specific capabilities, enforces execution boundaries and records every
action in an append-only audit trail. The workload — whether driven by an
LLM, a ruleset, or any other decision mechanism — operates within the
identity and resource envelope assigned to it.

## Architecture

```
                    ┌─────────────────────┐
                    │   VOXOrchestrator   │
                    │ (authority / fleet) │
                    └─────┬─────────┬─────┘
                          │         │
             ┌────────────┴───┐ ┌───┴────────────┐
             │  VOXWorkload 1 │ │  VOXWorkload 2 │
             │ -------------- │ │ -------------- │
             │  identity      │ │  identity      │
             │  roles         │ │  roles         │
             │  capabilities  │ │  capabilities  │
             └────────┬───────┘ └───────┬────────┘
                      │                 │
                ┌─────┴────┐       ┌────┴─────┐
                │ mounted  │       │ mounted  │
                │ resource │       │ resource │
                └──────────┘       └──────────┘
```

The runtime exposes two control interfaces:

- **Unix domain socket** (`/tmp/vox.sock`, owner-only) — operator lifecycle
  commands (`start`, `stop`, `restart`, `pause`, `resume`, `status`, `list`).
- **HTTP API** on port 8000 — fleet queries and lifecycle control for
  external tooling, authenticated via `VOX_API_TOKEN` Bearer token.

## Design principles

**Identity first.** Every workload has a persistent identity before
execution. Identity is validated at bootstrap and attached to every
audit record. No anonymous workloads.

**Explicit over implicit.** Manifests, roles, lifecycle transitions and
operator authentication are configured explicitly. Capabilities are
discovered by static analysis of role source code at bootstrap, not
by convention or filesystem magic.

**Least privilege.** A workload has access only to the capabilities,
filesystem paths and communication channels required by its roles.
Nothing is globally available.

**Immutable audit.** Every action is recorded in an append-only structured
log.

**Local-first infrastructure.** Backends run on infrastructure the operator
controls. No data leaves the local network unless explicitly configured.

## Core concepts

### Identity

Identity is the foundation of the permission model. Every workload has a UUID,
a name, an optional parent reference for hierarchical delegation, and an
operator identity enrolled via voice for privileged commands.

Identity is validated at bootstrap. A workload without a valid identity cannot
start.

### Orchestration

The orchestrator is the single authority for the fleet. It resolves
hierarchical dependencies, manages lifecycle transitions, discovers
capabilities and assigns them to workloads at mount time. The orchestrator
does not execute workload logic — it governs the conditions under which
workloads execute.

An optional file watcher (`WorkloadFileWatcher`) polls persona directories for
SHA-256 hash changes on `manifest.yml` and `roles/**/*.py`. When a change is
detected the orchestrator performs an isolated hot-restart of only the
affected workload — graceful shutdown, re-hire from disk with fresh manifest
and roles, then boot. Enabled by default; disable via `VOX_WATCH_DISABLED=true`.

### Capabilities

Capabilities are infrastructure resources — compute, communication, I/O —
that the orchestrator manages and assigns to workloads. They are not ambient
APIs. A workload can only use a capability that passes its health check.

Capability requirements are declared in role source code via the `REQUIRES`
class variable and `self.workload.capabilities["cap_id"]` usage. At bootstrap,
the workload scans its role files with an AST parser, resolves the required
capability set, and mounts the matching instances from the registry. If a
capability is unavailable, affected roles are disabled and the workload runs
in degraded mode.

A **Pub/Sub War Room** (`VOXWarRoom`) provides async incident notification.
Alerts published by any workload or the system are fanned out to all active
workloads and mirrored to an external channel through the orchestrator's
`FleetMessenger` singleton (boot-level Telegram broadcast, enabled when
`TELEGRAM_BOT_TOKEN` and `VOX_WAR_ROOM_ID` are configured). Role-level
messaging is handled by `comm.gateway` — a domain-agnostic multi-channel
gateway with adapter-driven inbound and outbound (Telegram via webhook or
`getUpdates` long-polling, WhatsApp Cloud API, generic HTTP webhook) —
mounted per workload as `CommGatewayCapability` and used via `send_text()`.

Built-in capabilities:

| Capability | ID | Backend |
|---|---|---|
| LLM text generation | `ai.llm` | Ollama (local) routed via complexity classifier (optional cloud fallback) |
| Headless browser | `net.browser` | Playwright (Firefox) |
| Messaging (inbound + outbound) | `comm.gateway` | Telegram (webhook / `getUpdates` long-poll) / WhatsApp Cloud API / generic webhook |
| Email dispatch | `comm.email` | SMTP |
| Speech-to-text | `comm.voicetotext` | faster-whisper |

The LLM is one capability among several. Capabilities are interchangeable.

### Roles

Roles are behavioural modules attached to a workload. A role declares which
capabilities it requires via `REQUIRES`, exposes command handlers via the
`@command` decorator, and subscribes to events via `role.on("event")`.
Only roles listed in the manifest are loaded.

Roles depend on capabilities, not the other way around. A role declares its
requirements in source code; the workload's AST scanner discovers them at
bootstrap.

### Lifecycle

Workloads follow a defined state machine:

`BOOTING → IDLE → ACTIVE ⇄ PAUSED → STOPPED ← FAILED`

Transitions pass through intermediate states (`PAUSING`, `STOPPING`,
`RESUMING`). The `PAUSED` state transfers authority from the workload back
to a human operator. While paused, inbound events are buffered in a bounded
queue (default 256 entries). On resume, buffered events replay in order.
This allows an operator to review decisions before they take effect.

The orchestrator initiates and controls all lifecycle transitions. Workloads do
not self-start or self-stop. Every transition is an authorisation decision
made by the orchestrator.

### Observability and forensics

Every workload maintains two private SQLite databases:

| Database | Class | File | Purpose |
|---|---|---|---|
| Activity log | `VOXWorkloadMemory` | `memory/logs.db` | Append-only structured event log |
| Asset store | `VOXWorkloadStore` | `memory/memory.db` | File asset index with checksum dedup |

Both databases are backed by **`aiosqlite`** — all I/O is fully asynchronous and
non-blocking. Every operation must be prefixed with `await`:

```python
event_id = await workload.memory.record("cmd", "execute", ref_id="...")
rows = await workload.store.query(
    "SELECT * FROM asset_index WHERE asset_type = ?", ("text",)
)
result = await workload.store.store_file(data, "report.pdf", "my_workload", "document")
```

Database schemas are created lazily during the workload's boot cycle via
`await workload.store.init_db()` and `await workload.memory.init_db()`, which run
after vault initialisation and before capability boot in `VOXWorkload.boot()`.
Object instantiation and connection setup are decoupled — the `VOXWorkloadStore`
and `VOXWorkloadMemory` objects are created in `__init__`, but the actual
database files and tables are not created until the async boot phase.

Console output uses coloured ANSI formatters; file output uses plain text.

### Control plane

A Unix domain socket at `/tmp/vox.sock` (permissions `0o600`) accepts
lifecycle commands. The socket is owner-only — no network exposure.

## Security model

Security is not a collection of independent features. It emerges from how
identity, capabilities, lifecycle, filesystem and communication are
structured.

- **Identity enforcement.** Every workload must present a valid manifest with
  name and UUID. Bootstrap rejects workloads without valid identity.
- **Capability gating.** A role whose required capabilities cannot be mounted
  is disabled at bootstrap. The workload runs in degraded mode; it cannot use
  capabilities that were never mounted.
- **Filesystem isolation.** File access is scoped to a per-workload sandbox
  directory. Operations outside the sandbox are rejected at the path level.
- **Control plane isolation.** The Unix domain socket is restricted to the
  owning user.
- **Hierarchical communication isolation.** Workloads communicate only with
  their direct parent or direct children, never laterally.
- **Operator authentication.** A pre-enrolled voice embedding
  (`VOXSpeakerProfile`) is verified via cosine similarity before privileged
  operations. All processing is local — no data leaves the machine.
- **Explicit role loading.** Only roles listed in the workload's manifest are
  loaded. Code in the roles directory not listed in the manifest is ignored.
- **Input sanitization.** Every inbound payload passes through a pattern
  scanner (`InputSanitizer`) before reaching any workload. SQLi, XSS, command
  injection, path traversal and code execution signatures are blocked at the
  orchestrator and HTTP API entrypoints.
- **Per-workload rate limiting.** Each workload enforces a sliding-window rate
  limit (`RateLimiter`) on event emissions. Limits are configurable per workload
  via manifest keys (`rate_limit_max_calls`, `rate_limit_window`); on breach
  the event is dropped and a critical alert is logged.
- **Append-only audit.** Forensic logs cannot be modified or deleted after
  creation. Every record carries the workload identity.
- **Encrypted secret vault.** Per-workload AES-256-GCM vault (`WorkloadVault`) with
  PBKDF2HMAC key derivation. Each workload gets an isolated `secrets.vault`
  SQLite file. Fallback chain: vault → local `.env`.

## Getting started

### Prerequisites

- Python 3.11+
- [Ollama](https://ollama.ai) (for `ai.llm` capability)
- [Playwright](https://playwright.dev) browsers: `playwright install firefox`
- Telegram Bot Token — fleet-level War Room / `FleetMessenger` alert mirroring, and the optional `comm.gateway` Telegram adapter (only if you use Telegram)
- WhatsApp Cloud API credentials — optional, only for the `comm.gateway` WhatsApp adapter

### Installation

```
git clone <repo> && cd vox
python -m venv .venv && source .venv/bin/activate
pip install -e .
playwright install firefox
```

### Configuration

Minimal `.env` in the project root:

```
VOX_WAR_ROOM_ID=123456789
TELEGRAM_BOT_TOKEN=...
TELEGRAM_USER_ID=...
```

| Variable | Default | Purpose |
|---|---|---|
| `VOX_MASTER_KEY` | — | Master passphrase for the per-workload encrypted vault (`WorkloadVault`). Required when capabilities declare `SENSITIVE_PARAMS` that are not provided via `.env`. |
| `VOX_WAR_ROOM_ID` | — | Telegram chat ID for War Room alert mirroring. **Required** — VOX refuses to start without it. |
| `TELEGRAM_BOT_TOKEN` | — | Telegram Bot API token — fleet-level War Room / `FleetMessenger` alert mirroring, and the optional `comm.gateway` Telegram adapter |
| `TELEGRAM_USER_ID` | — | Telegram chat/user ID injected into every workload config (global workload key) and used as the outbound recipient for `comm.gateway` Telegram messages |
| `LLM_API_BASE_URL` | `http://localhost:11434` | Ollama endpoint for `ai.llm` capability (capability param) |
| `TELEGRAM_LONG_TIMEOUT` | `25` | `comm.gateway` Telegram `getUpdates` long-poll timeout (seconds); the HTTP client read timeout is derived from it (+5s buffer) |
| `VOX_API_HOST` | `127.0.0.1` | HTTP API bind address |
| `VOX_API_TOKEN` | — | Bearer token for API authentication |
| `VOX_WATCH_DISABLED` | — | Set to `true` to disable the workload file watcher |
| `VOX_VERBOSE_LOGGING` | `false` | Enable verbose debug logging |
| `VOX_UDS_PATH` | `/tmp/vox.sock` | Unix domain socket path |

Channel credentials for the optional `comm.gateway` adapters are adapter-owned and only required for the channel you use — e.g. the WhatsApp Cloud API adapter reads `WHATSAPP_ACCESS_TOKEN` and `WHATSAPP_PHONE_NUMBER_ID` (plus `WHATSAPP_APP_SECRET` / `WHATSAPP_VERIFY_TOKEN` when webhook verification is enabled). `TELEGRAM_BOT_TOKEN` additionally powers fleet-level War Room mirroring via `FleetMessenger` when `VOX_WAR_ROOM_ID` is set.

The HTTP API always binds to port `8000` (fixed in `api_server.py`, no env override).

Configuration precedence: **CLI args > Environment variables (`.env` + `os.environ`) > Code defaults**.

### Enrolling a speaker identity

```
python tools/enroll_speaker.py
```

Records five voice samples and saves the averaged embedding to
`identity/master_voice.npy`.

### Running

```
vox
```

Starts the orchestrator, reads workload manifests from the filesystem, boots
workloads with `autostart: true`, and serves the control interfaces.

### Lifecycle commands

```
vox start <name>          Boot a workload
vox stop <name>           Stop a workload
vox restart <name>        Restart a workload
vox pause <name>          Pause (buffers events for human review)
vox resume <name>         Replay buffered events
```

Fleet snapshot and registry queries (`status`, `list`) are served by the UDS
control plane but are not currently exposed through the `vox` CLI.

### Creating a workload

```
instance/personas/
  my_workload/
    manifest.yml          name, id, master_id, roles, personality, rate limits
    .env                   optional per-workload secrets override
    roles/
      handler.py           VOXRole subclass with @command handlers
```

The manifest declares the workload's identity and which roles to load.
Capability requirements are inferred from role source code at bootstrap
via AST analysis. Required identity fields: `name`, `id`.

## Storage architecture

All SQLite storage is fully asynchronous via `aiosqlite`:

| Class | File | Module |
|---|---|---|
| `VOXWorkloadStore` | `memory/memory.db` | `src/vox/workloads/store.py` |
| `VOXWorkloadMemory` | `memory/logs.db` | `src/vox/workloads/memory.py` |
| `SemanticCache` | `llm_cache.db` | `src/vox/capabilities/ai/llm/cache.py` |
| `WorkloadVault` | `secrets.vault` | `src/vox/security/vault.py` |
| `RAGRetriever` | workload `memory.db` | `src/vox/capabilities/ai/llm/rag.py` |

`WorkloadVault` retains synchronous `sqlite3` inside `__init__` only (one-time
bootstrap — schema creation + salt derivation). All runtime public methods
(`get()`, `set()`, etc.) are async via `aiosqlite`.

## Project structure

```
vox/
├── instance/personas/                    runtime workload directories
├── identity/                             enrolled operator voice embedding
├── tools/enroll_speaker.py               voice enrolment utility
├── tools/inspect_workload.py             per-workload diagnostics
├── tools/inspect_vault.py                vault inspection / purge
├── tools/provision_vault.py              interactive vault secret provisioning
├── src/vox/
│   ├── cli.py                            CLI entry point, UDS client
│   ├── __main__.py                       python -m vox entry point
│   ├── workloads/                        workload runtime, loader, AST analyzer
│   │   ├── base.py                       VOXWorkload core
│   │   ├── loader.py                     manifest parsing & identity validation
│   │   ├── ast_analyzer.py               static capability dependency scanner
│   │   ├── capability_binder.py          mounts capabilities to workload roles
│   │   ├── lifecycle.py                  state machine (BOOTING…FAILED)
│   │   ├── memory.py                     VOXWorkloadMemory — append-only event log
│   │   └── store.py                      VOXWorkloadStore — file asset index
│   ├── capabilities/                     capability definitions and backends
│   │   ├── base.py                       VOXCapability, VOXBoundCapability
│   │   ├── ai/llm/                       unified LLM pipeline
│   │   │   ├── capability.py             ai.llm capability entry point
│   │   │   ├── client.py                 Ollama HTTP client
│   │   │   ├── cache.py                  semantic response cache (SHA-256 + TTL)
│   │   │   ├── rag.py                    FTS5 retrieval-augmented generation
│   │   │   ├── router.py                 complexity classifier (local vs cloud)
│   │   │   ├── sanitizer.py              input control-char/boilerplate cleaning
│   │   │   └── models.py                 LLM request/response types
│   │   ├── net/browser/                  headless browser via Playwright
│   │   ├── comm/gateway/                 multi-channel communication gateway
│   │   │   ├── capability.py             CommGatewayCapability wrapper
│   │   │   ├── models.py                 VOXInboundMessage / VOXOutboundMessage
│   │   │   ├── server.py                 IngressServer (shared aiohttp listener)
│   │   │   ├── adapters/
│   │   │   │   ├── base.py               BaseAdapter ABC
│   │   │   │   ├── telegram.py           Telegram Bot API adapter
│   │   │   │   ├── whatsapp.py           WhatsApp Cloud API adapter
│   │   │   │   └── webhook.py            generic HTTP webhook adapter
│   │   │   └── capability.yml            capability manifest
│   │   ├── comm/email/                   SMTP email dispatch
│   │   └── comm/voicetotext/             speech-to-text via faster-whisper
│   ├── config/                           configuration loading & resolution
│   │   ├── models.py                     VOXConfig dataclass
│   │   ├── from_env.py                   .env / os.environ parsing
│   │   ├── from_cli.py                   argparse CLI argument parsing
│   │   ├── resolver.py                   merge CLI + env → VOXConfig
│   │   └── loader.py                     load_config() convenience entry
│   ├── messaging/                        inter-workload message envelope schema
│   │   └── models.py                     VOXMessage dataclass
│   ├── observability/                    forensic logger, ANSI formatters
│   │   ├── models.py                     VOXForensicLogger, VOXLogSource
│   │   ├── formatters.py                 colourized console / plain file formatters
│   │   └── constants.py                  custom log levels
│   ├── orchestration/                    registry, graph, controller, war room
│   │   ├── base.py                       VOXOrchestrator — fleet coordinator
│   │   ├── registry.py                   capability & workload discovery from disk
│   │   ├── graph.py                      FleetGraph — workload hierarchy resolver
│   │   ├── controller.py                 FleetController — per-workload lifecycle
│   │   ├── war_room.py                   VOXWarRoom — async Pub/Sub alert queue
│   │   └── watcher.py                    WorkloadFileWatcher — hot-reload monitor
│   ├── roles/                            VOXRole base, @command decorator
│   ├── runtime/                          bootstrap, control plane, daemon
│   │   ├── models.py                     VOXRuntime container dataclass
│   │   ├── daemon.py                     async main loop, start API + UDS
│   │   ├── control_plane.py              UDS request handlers
│   │   └── factory.py                    build_vox() wiring assembly
│   ├── security/                         input sanitizer, rate limiter, vault, voice
│   │   ├── vault.py                      WorkloadVault — AES-256-GCM per-workload store
│   │   ├── guardrails.py                 InputSanitizer — WAF pattern scanner
│   │   ├── rate_limiter.py               sliding-window event rate limiter
│   │   └── speaker_profile.py            VOXSpeakerProfile — cosine-similarity verification
│   ├── services/                         fleet-wide infrastructure
│   │   └── fleet_messenger.py            FleetMessenger — boot-level Telegram broadcast
│   ├── api_server.py                     HTTP API (aiohttp, auth + guardrail middleware)
│   └── provider.py                       CapabilityProviderProtocol protocol
├── tests/
└── pyproject.toml
```

## Development

```
pip install -e ".[dev]"
python -m pytest tests/
```
