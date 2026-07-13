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

- **Unix domain socket** — operator lifecycle commands (`start`, `stop`,
  `pause`, `resume`, `status`, `list`). Access is restricted to the owning
  user.
- **HTTP API** on port 8000 — fleet queries and lifecycle control for
  external tooling.

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

Built-in capabilities:

| Capability | ID | Backend |
|---|---|---|
| LLM text generation | `ai.ollama` | Ollama |
| Headless browser | `net.browser` | Playwright (Firefox) |
| Messaging | `comm.telegram` | Telegram Bot API |
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

The `PAUSED` state transfers authority from the workload back to a human
operator. While paused, inbound events are buffered in a bounded queue
(default 256 entries). On resume, buffered events replay in order. This
allows an operator to review decisions before they take effect.

The orchestrator initiates and controls all lifecycle transitions. Agents do
not self-start or self-stop. Every transition is an authorisation decision
made by the orchestrator.

### Observability and forensics

Every agent maintains an append-only SQLite activity log. Records are never
modified or deleted after creation. Each record carries the agent's identity
and a causality chain.

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
- **Operator authentication.** Speaker verification (Resemblyzer) is
  required before privileged operations. All processing is local — no data
  leaves the machine.
- **Explicit role loading.** Only roles listed in the agent's manifest are
  loaded. Code in the roles directory not listed in the manifest is ignored.
- **Append-only audit.** Forensic logs cannot be modified or deleted after
  creation. Every record carries the agent identity.

## Getting started

### Prerequisites

- Python 3.11+
- [Ollama](https://ollama.ai) (for LLM capability)
- [Playwright](https://playwright.dev) browsers: `playwright install firefox`
- Telegram Bot Token (for `comm.telegram`)

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
VOX_OLLAMA_URL=http://localhost:11434
VOX_API_PORT=8000
```

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
vox status                Fleet snapshot
vox list                  Agent registry
vox start <name>          Boot an agent
vox stop <name>           Stop an agent
vox restart <name>        Restart an agent
vox pause <name>          Pause (buffers events for human review)
vox resume <name>         Replay buffered events
```

### Creating an agent

```
agents/
  my_agent/
    agent.yml          name, id, master_id, roles, personality
    roles/
      handler.py       VOXRole subclass with @command handlers
```

The manifest declares the agent's identity and which roles to load.
Capability requirements are inferred from role source code at bootstrap.

## Project structure

```
vox/
├── identity/                          enrolled operator voice embedding
├── tools/enroll_speaker.py            voice enrolment utility
├── src/vox/
│   ├── cli.py                         CLI entry point, UDS client
│   ├── agents/                        agent, memory, store, lifecycle
│   ├── capabilities/                  capability definitions and backends
│   │   ├── ai/ollama/                 LLM via Ollama
│   │   ├── net/browser/               headless browser via Playwright
│   │   ├── comm/telegram/             Telegram messaging
│   │   └── comm/voicetotext/          speech-to-text via faster-whisper
│   ├── config/                        configuration loading and resolution
│   ├── messaging/                     inter-agent message envelope schema
│   ├── observability/                 forensic logger, formatters
│   ├── orchestration/                 fleet controller, capability gating
│   ├── roles/                         VOXRole base, @command decorator
│   ├── runtime/                       bootstrap, control plane
│   ├── security/                      voice verification
│   └── services/api_server/           HTTP API
├── tests/
├── pyproject.toml
└── todo.md
```

## Development

```
pip install -e ".[dev]"
python -m pytest tests/
```
