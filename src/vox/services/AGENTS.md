# AGENTS.md — services silo

Normative working rules for the VOX services layer in `src/vox/services/`.
This is agent instruction, not project documentation. It defines the **target
architecture**: the boundary the services layer must converge on, not a record
of every current internal. Existing code that conflicts with the boundary is
explicitly labeled NON-CONFORMANT and to be closed, never treated as accepted
architecture.

This contract is checked against the sibling AGENTS.md documents in
`capabilities`, `workloads`, `orchestration`, `runtime`, `config`, `security`,
and `observability`; those contracts are established normative architecture.

## Responsibility

The services silo is a **leaf service package** that owns external transport
adapters used for fleet-level notification. It is deliberately distinct from
`vox.messaging` (the message-model contract silo): `services` owns the *outward
transport integration*, `messaging` owns the *message representation*. There is
no dependency between the two — keep it that way.

Owned here:

- `FleetMessenger` — a thin HTTP transport adapter that posts plain-text
  operational alerts to a configured external channel (Telegram) via `httpx`,
  and its HTTP-client lifecycle/state (`_client`).
- Delivery/error handling for that transport: `post_message` swallows send
  failure (logs via the established observability surface, returns bool).

Not owned here:

- **Message construction/models** — owned by `vox.messaging`.
- **Fleet/workload notification policy** (what gets sent, to whom, when) —
  owned by the orchestration war-room dispatcher (`VOXWarRoomMaster`).
- **Recipient/address resolution** — supplied by the constructing side
  (`channel_id`, `bot_token` are passed in); `FleetMessenger` does not resolve
  addresses.
- **Secret ownership/lifecycle** — the `bot_token` is an environment-backed
  secret reference resolved at bootstrap and handed into the adapter; the
  adapter uses it for the external API and must never log, persist, or commit it.

## Boundaries

### Out of the silo (imports/leaves)
`FleetMessenger` imports only `vox.observability` (for the forensic logger) and
`httpx`. It must **not** import or reach into orchestration, workload, runtime,
API server, capability, security, or messaging internals.

### Transport adapter, not orchestration
`FleetMessenger` is a pure adapter: it converts `(text) → external channel`. It
contains no fleet/routing policy and never touches a workload, a `VOXWorkload`
private, or an orchestrator. The orchestrator owns construction and lifecycle
(creates it, wires `post_message` as the war-room broadcast callback, calls
`shutdown`).

## Architectural invariants (target)

1. The services silo imports only `vox.observability`, `httpx`, and stdlib.
2. `FleetMessenger` never references workload/orchestrator internals or
   `vox.messaging` models.
3. The secret `bot_token` is passed in via the constructor and used only for the
   transport call; it is never logged, serialized, or committed.
4. External transport errors are handled inside the adapter (logged +
   bool return), not propagated as unhandled failures to fleet policy.
5. The public surface is exactly `FleetMessenger` (`__all__`).

## What constitutes an architectural change

- adding a transport adapter or external notification channel;
- changing the `post_message`/`shutdown` signature or lifecycle semantics;
- adding a dependency on `vox.messaging`, orchestration, workload, or a new silo;
- moving recipient resolution or notification policy into the services silo.

## Testing / architecture enforcement

- `pytest` (`python -m pytest`, asyncio auto mode). Behavior changes ship with
  tests in `tests/test_*.py`. Outbound HTTP is mocked with `httpx.MockTransport`.
- `tests/test_services_arch.py` guards the silo: leaf-import boundary, no
  orchestration/workload coupling, no secret serialization, and a single public
  construction site (orchestration).

## Known boundary debt (current vs. target)

None observed. `FleetMessenger` is a clean leaf transport adapter consumed only
by orchestration.