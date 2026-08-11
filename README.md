# VOX

VOX is an identity-first orchestration platform for autonomous workloads. It
manages long-lived execution units with persistent identity, explicit resource
grants, and independent audit storage.

VOX separates reasoning from authority. AI models may produce decisions, but
the platform controls what those decisions can act upon. Capabilities,
filesystem access, communication paths and execution permissions are governed
by the orchestrator, not by the workload itself. This is the central
architectural invariant: **orchestration owns authority; agents never own
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
             │  VOXAgent 1    │ │  VOXAgent 2    │
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

Identity is the foundation of the permission model. Every agent has a UUID,
a name, an optional parent reference for hierarchical delegation, and an
operator identity enrolled via voice for privileged commands.

Identity is validated at bootstrap. An agent without a valid identity cannot
start.

### Orchestration

The orchestrator is the single authority for the fleet. It resolves
hierarchical dependencies, manages lifecycle transitions, discovers
capabilities and assigns them to agents at mount time. The orchestrator
does not execute workload logic — it governs the conditions under which
workloads execute.

An optional file watcher (`AgentFileWatcher`) polls agent directories for
SHA-256 hash changes on `agent.yml` and `roles/**/*.py`. When a change is
detected the orchestrator performs an isolated hot-restart of only the
affected agent — graceful shutdown, re-hire from disk with fresh manifest
and roles, then boot. Enabled by default; disable via `VOX_WATCH_DISABLED=true`.

### Capabilities

Capabilities are infrastructure resources — compute, communication, I/O —
that the orchestrator manages and assigns to agents. They are not ambient
APIs. An agent can only use a capability that passes its health check.

Capability requirements are declared in role source code via the `REQUIRES`
class variable and `self.agent.capabilities["cap_id"]` usage. At bootstrap,
the agent scans its role files with an AST parser, resolves the required
capability set, and mounts the matching instances from the registry. If a
capability is unavailable, affected roles are disabled and the agent runs
in degraded mode.

A **Pub/Sub War Room** (`VOXWarRoom`) provides async incident notification.
Alerts published by any agent or the system are fanned out to all active
agents and mirrored to an external channel through the orchestrator's
`FleetMessenger` singleton (boot-level Telegram broadcast, enabled when
`TELEGRAM_BOT_TOKEN` and `VOX_WAR_ROOM_ID` are configured). Role-level
messaging is handled by `comm.gateway` — a domain-agnostic multi-channel
gateway with adapter-driven inbound and outbound (Telegram via webhook or
`getUpdates` long-polling, generic HTTP webhook) — mounted per agent as
`CommGatewayCapability` and used via `send_broadcast()`.

Built-in capabilities:

| Capability | ID | Backend |
|---|---|---|
| LLM text generation | `ai.llm` | Ollama (local) routed via complexity classifier (optional cloud fallback) |
| Headless browser | `net.browser` | Playwright (Firefox) |
| Messaging (inbound + broadcast) | `comm.gateway` | Telegram (webhook / `getUpdates` long-poll) / generic webhook |
| Email dispatch | `comm.email` | SMTP |
| Speech-to-text | `comm.voicetotext` | faster-whisper |

The LLM is one capability among several. Capabilities are interchangeable.

### Roles

Roles are behavioural modules attached to an agent. A role declares which
capabilities it requires via `REQUIRES`, exposes command handlers via the
`@command` decorator, and subscribes to events via `role.on("event")`.
Only roles listed in the manifest are loaded.

Roles depend on capabilities, not the other way around. A role declares its
requirements in source code; the agent's AST scanner discovers them at
bootstrap.

### Lifecycle

Agents follow a defined state machine:

`BOOTING → IDLE → ACTIVE ⇄ PAUSED → STOPPED ← FAILED`

Transitions pass through intermediate states (`PAUSING`, `STOPPING`,
`RESUMING`). The `PAUSED` state transfers authority from the workload back
to a human operator. While paused, inbound events are buffered in a bounded
queue (default 256 entries). On resume, buffered events replay in order.
This allows an operator to review decisions before they take effect.

The orchestrator initiates and controls all lifecycle transitions. Agents do
not self-start or self-stop. Every transition is an authorisation decision
made by the orchestrator.

### Observability and forensics

Every agent maintains two private SQLite databases:

| Database | Class | File | Purpose |
|---|---|---|---|
| Activity log | `VOXAgentMemory` | `memory/logs.db` | Append-only structured event log |
| Asset store | `VOXAgentStore` | `memory/memory.db` | File asset index with checksum dedup |

Both databases are backed by **`aiosqlite`** — all I/O is fully asynchronous and
non-blocking. Every operation must be prefixed with `await`:

```python
event_id = await agent.memory.record("cmd", "execute", ref_id="...")
rows = await agent.store.query(
    "SELECT * FROM asset_index WHERE asset_type = ?", ("text",)
)
result = await agent.store.store_file(data, "report.pdf", "my_agent", "document")
```

Database schemas are created lazily during the agent's boot cycle via
`await agent.store.init_db()` and `await agent.memory.init_db()`, which run
after vault initialisation and before capability boot in `VOXAgent.boot()`.
Object instantiation and connection setup are decoupled — the `VOXAgentStore`
and `VOXAgentMemory` objects are created in `__init__`, but the actual
database files and tables are not created until the async boot phase.

Console output uses coloured ANSI formatters; file output uses plain text.

### Control plane

A Unix domain socket at `/tmp/vox.sock` (permissions `0o600`) accepts
lifecycle commands. The socket is owner-only — no network exposure.

## Security model

Security is not a collection of independent features. It emerges from how
identity, capabilities, lifecycle, filesystem and communication are
structured.

- **Identity enforcement.** Every agent must present a valid manifest with
  name and UUID. Bootstrap rejects agents without valid identity.
- **Capability gating.** A role whose required capabilities cannot be mounted
  is disabled at bootstrap. The agent runs in degraded mode; it cannot use
  capabilities that were never mounted.
- **Filesystem isolation.** File access is scoped to a per-agent sandbox
  directory. Operations outside the sandbox are rejected at the path level.
- **Control plane isolation.** The Unix domain socket is restricted to the
  owning user.
- **Hierarchical communication isolation.** Agents communicate only with
  their direct parent or direct children, never laterally.
- **Operator authentication.** A pre-enrolled voice embedding
  (`VOXSpeakerProfile`) is verified via cosine similarity before privileged
  operations. All processing is local — no data leaves the machine.
- **Explicit role loading.** Only roles listed in the agent's manifest are
  loaded. Code in the roles directory not listed in the manifest is ignored.
- **Input sanitization.** Every inbound payload passes through a pattern
  scanner (`InputSanitizer`) before reaching any agent. SQLi, XSS, command
  injection, path traversal and code execution signatures are blocked at the
  orchestrator and HTTP API entrypoints.
- **Per-agent rate limiting.** Each agent enforces a sliding-window rate
  limit (`RateLimiter`) on event emissions. Limits are configurable per agent
  via manifest keys (`rate_limit_max_calls`, `rate_limit_window`); on breach
  the event is dropped and a critical alert is logged.
- **Append-only audit.** Forensic logs cannot be modified or deleted after
  creation. Every record carries the agent identity.
- **Encrypted secret vault.** Per-agent AES-256-GCM vault (`AgentVault`) with
  PBKDF2HMAC key derivation. Each agent gets an isolated `secrets.vault`
  SQLite file. Fallback chain: vault → local `.env`.

## Getting started

### Prerequisites

- Python 3.11+
- [Ollama](https://ollama.ai) (for `ai.llm` capability)
- [Playwright](https://playwright.dev) browsers: `playwright install firefox`
- Telegram Bot Token (for `comm.gateway` Telegram adapter)

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
| `VOX_MASTER_KEY` | — | Master passphrase for the per-agent encrypted vault (`AgentVault`). Required when capabilities declare `SENSITIVE_PARAMS` that are not provided via `.env`. |
| `VOX_WAR_ROOM_ID` | — | Telegram chat ID for War Room alert mirroring. **Required** — VOX refuses to start without it. |
| `TELEGRAM_BOT_TOKEN` | — | Telegram Bot API token for messenger connectors |
| `TELEGRAM_USER_ID` | — | Telegram chat/user ID for per-agent broadcast delivery |
| `LLM_API_BASE_URL` | `http://localhost:11434` | Ollama endpoint for `ai.llm` capability (capability param) |
| `TELEGRAM_LONG_TIMEOUT` | `25` | `comm.gateway` Telegram `getUpdates` long-poll timeout (seconds); the HTTP client read timeout is derived from it (+5s buffer) |
| `VOX_API_HOST` | `127.0.0.1` | HTTP API bind address |
| `VOX_API_TOKEN` | — | Bearer token for API authentication |
| `VOX_WATCH_DISABLED` | — | Set to `true` to disable the agent file watcher |
| `VOX_VERBOSE_LOGGING` | `false` | Enable verbose debug logging |
| `VOX_UDS_PATH` | `/tmp/vox.sock` | Unix domain socket path |

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

Starts the orchestrator, reads agent manifests from the filesystem, boots
agents with `autostart: true`, and serves the control interfaces.

### Lifecycle commands

```
vox start <name>          Boot an agent
vox stop <name>           Stop an agent
vox restart <name>        Restart an agent
vox pause <name>          Pause (buffers events for human review)
vox resume <name>         Replay buffered events
```

Fleet snapshot and registry queries (`status`, `list`) are served by the UDS
control plane but are not currently exposed through the `vox` CLI.

### Creating an agent

```
agents/
  my_agent/
    agent.yml          name, id, master_id, roles, personality, rate limits
    .env               optional per-agent secrets override
    roles/
      handler.py       VOXRole subclass with @command handlers
```

The manifest declares the agent's identity and which roles to load.
Capability requirements are inferred from role source code at bootstrap
via AST analysis. Required identity fields: `name`, `id`.

## Storage architecture

All SQLite storage is fully asynchronous via `aiosqlite`:

| Class | File | Module |
|---|---|---|
| `VOXAgentStore` | `memory/memory.db` | `src/vox/agents/store.py` |
| `VOXAgentMemory` | `memory/logs.db` | `src/vox/agents/memory.py` |
| `SemanticCache` | `llm_cache.db` | `src/vox/capabilities/ai/llm/cache.py` |
| `AgentVault` | `secrets.vault` | `src/vox/security/vault.py` |
| `RAGRetriever` | agent `memory.db` | `src/vox/capabilities/ai/llm/rag.py` |

`AgentVault` retains synchronous `sqlite3` inside `__init__` only (one-time
bootstrap — schema creation + salt derivation). All runtime public methods
(`get()`, `set()`, etc.) are async via `aiosqlite`.

## Project structure

```
vox/
├── identity/                            enrolled operator voice embedding
├── tools/enroll_speaker.py              voice enrolment utility
├── tools/inspect_agent.py               per-agent diagnostics
├── tools/inspect_vault.py               vault inspection / purge
├── tools/provision_vault.py             interactive vault secret provisioning
├── src/vox/
│   ├── cli.py                           CLI entry point, UDS client
│   ├── __main__.py                      python -m vox entry point
│   ├── agents/                          agent runtime, loader, AST analyzer
│   │   ├── base.py                      VOXAgent core
│   │   ├── loader.py                    manifest parsing & identity validation
│   │   ├── ast_analyzer.py              static capability dependency scanner
│   │   ├── capability_binder.py         mounts capabilities to agent roles
│   │   ├── lifecycle.py                 state machine (BOOTING…FAILED)
│   │   ├── memory.py                    VOXAgentMemory — append-only event log
│   │   └── store.py                     VOXAgentStore — file asset index
│   ├── capabilities/                    capability definitions and backends
│   │   ├── base.py                      VOXCapability, VOXBoundCapability
│   │   ├── ai/llm/                      unified LLM pipeline
│   │   │   ├── capability.py            ai.llm capability entry point
│   │   │   ├── client.py                Ollama HTTP client
│   │   │   ├── cache.py                 semantic response cache (SHA-256 + TTL)
│   │   │   ├── rag.py                   FTS5 retrieval-augmented generation
│   │   │   ├── router.py                complexity classifier (local vs cloud)
│   │   │   ├── sanitizer.py             input control-char/boilerplate cleaning
│   │   │   └── models.py                LLM request/response types
│   │   ├── net/browser/                 headless browser via Playwright
│   │   ├── comm/gateway/                  multi-channel communication gateway
│   │   │   ├── capability.py            CommGatewayCapability wrapper
│   │   │   ├── models.py                VOXInboundMessage / VOXOutboundMessage
│   │   │   ├── server.py                IngressServer (shared aiohttp listener)
│   │   │   ├── adapters/
│   │   │   │   ├── base.py              BaseAdapter ABC
│   │   │   │   ├── telegram.py          Telegram Bot API adapter
│   │   │   │   └── webhook.py           generic HTTP webhook adapter
│   │   │   └── capability.yml           capability manifest
│   │   ├── comm/email/                  SMTP email dispatch
│   │   └── comm/voicetotext/            speech-to-text via faster-whisper
│   ├── config/                          configuration loading & resolution
│   │   ├── models.py                    VOXConfig dataclass
│   │   ├── from_env.py                  .env / os.environ parsing
│   │   ├── from_cli.py                  argparse CLI argument parsing
│   │   ├── resolver.py                  merge CLI + env → VOXConfig
│   │   └── loader.py                    load_config() convenience entry
│   ├── messaging/                       inter-agent message envelope schema
│   │   └── models.py                    VOXMessage dataclass
│   ├── observability/                   forensic logger, ANSI formatters
│   │   ├── models.py                    VOXForensicLogger, VOXLogSource
│   │   ├── formatters.py                colourized console / plain file formatters
│   │   └── constants.py                 custom log levels
│   ├── orchestration/                   registry, graph, controller, war room
│   │   ├── base.py                      VOXOrchestrator — fleet coordinator
│   │   ├── registry.py                  capability & agent discovery from disk
│   │   ├── graph.py                     agent hierarchy resolver
│   │   ├── controller.py                per-agent lifecycle (stop/restart/pause)
│   │   ├── war_room.py                  VOXWarRoom — async Pub/Sub alert queue
│   │   └── watcher.py                   AgentFileWatcher — hot-reload monitor
│   ├── roles/                           VOXRole base, @command decorator
│   ├── runtime/                         bootstrap, control plane, daemon
│   │   ├── models.py                    VOXRuntime container dataclass
│   │   ├── daemon.py                    async main loop, start API + UDS
│   │   ├── control_plane.py             UDS request handlers
│   │   └── factory.py                   build_vox() wiring assembly
│   ├── security/                        input sanitizer, rate limiter, vault, voice
│   │   ├── vault.py                     AgentVault — AES-256-GCM per-agent store
│   │   ├── guardrails.py                InputSanitizer — WAF pattern scanner
│   │   ├── rate_limiter.py              sliding-window event rate limiter
│   │   └── speaker_profile.py           VOXSpeakerProfile — cosine-similarity verification
│   ├── services/                        fleet-wide infrastructure
│   │   └── fleet_messenger.py           FleetMessenger — boot-level Telegram broadcast
│   ├── api_server.py                    HTTP API (aiohttp, auth + guardrail middleware)
│   └── provider.py                      CapabilityProviderProtocol protocol
├── docs/
│   └── internal/
│       └── todo.md                      roadmap
├── tests/
└── pyproject.toml
```

## Development

```
pip install -e ".[dev]"
python -m pytest tests/
```
