# AGENTS.md — messaging silo

Normative working rules for the VOX messaging layer in `src/vox/messaging/`.
This is agent instruction, not project documentation. It defines the **target
architecture**: the boundary the messaging layer must converge on, not a record
of every current internal. Existing code that conflicts with the boundary is
explicitly labeled NON-CONFORMANT and to be closed, never treated as accepted
architecture.

This contract is checked against the sibling AGENTS.md documents in
`capabilities`, `workloads`, `orchestration`, `runtime`, `config`, `security`,
`observability`, and `services`; those contracts are established normative
architecture.

## Responsibility

The messaging silo is a **leaf contract/model package** that owns the standard
inter-workload message envelope and the delegation-protocol message-type
constants. It is deliberately distinct from `vox.services` (the transport-adapter
silo): `messaging` owns the *message representation/contract*, `services` owns
the *outward transport integration*. There is no dependency between the two —
keep it that way.

Owned here:

- `VOXMessage` — the frozen pydantic envelope for inter-workload and
  war-room/alert messages (`message_id`, `message_source`, `emitted_at`,
  `source`, `target`, `type`, `details`, `reply_to`) and its ISO-8601
  `emitted_at` validation.
- `MSG_COMMAND_REQUEST`, `MSG_ACKNOWLEDGED`, `MSG_COMMAND_RESULT`,
  `MSG_COMMAND_ERROR` — the delegation-protocol message-type constants.

Not owned here:

- **Transport/outward integration** — owned by `vox.services`.
- **Which layer builds or consumes a `VOXMessage`** — the constructing side
  (e.g. orchestration war room) chooses; messaging only defines the contract.

## Boundaries

### Out of the silo (imports/leaves)
The messaging package imports only stdlib (`datetime`, `uuid`, `typing`) and
`pydantic`. It imports **no** `vox.*` package — it is a true leaf model package.
It must never import `vox.services`, `vox.orchestration`, `vox.workloads`,
`vox.config`, or observability.

### Consumers use the public root
The public surface (`__init__.__all__`) exposes `VOXMessage` and the `MSG_*`
constants. Consumers should import from the package root
(`from vox.messaging import VOXMessage`). Importing from the submodule
(`vox.messaging.models`) is a consumer-side deep path; the messaging silo keeps
`VOXMessage` on the root so consumers do not need it.

## Architectural invariants (target)

1. The messaging package imports only stdlib and pydantic — no `vox.*` imports.
2. `VOXMessage` and the `MSG_*` protocol constants are exported from the package
   root, so consumers use the public surface.
3. The messaging silo owns no transport, no workload/ fleet policy, and no
   observability/security/config coupling.
4. Message envelopes are immutable (`frozen`) and validated by the model; the
   silo does not enforce routing or delivery semantics.

## What constitutes an architectural change

- changing the `VOXMessage` schema/contract or its validation;
- removing or renaming a `MSG_*` protocol constant;
- adding any `vox.*` (non-leaf) dependency.

## Testing / architecture enforcement

- `pytest` (`python -m pytest`, asyncio auto mode). Behavior changes ship with
  tests in `tests/test_*.py`.
- `tests/test_messaging_arch.py` guards the silo: leaf-import boundary, public
  surface completeness, no coupling to `vox.services`, and no new consumer deep
  imports of messaging submodules.
- `tests/test_messaging_models.py` guards `VOXMessage` behavior.

## Known boundary debt (current vs. target)

- **Reported — `MSG_*` constants are currently unreferenced.** The four
  delegation-protocol constants are exported but no production code constructs
  or consumes them. They encode a documented protocol (see the module docstring)
  and are treated as forward-looking contract surface, so they are **kept**, not
  pruned. If no consumer materializes across the protocol implementation, they
  should be removed deliberately (a messaging-silo decision), never as an
  incidental edit.
- **Reported (consumer side) — orchestration reaches messaging submodule.**
  `orchestration/war_room.py` imports `from vox.messaging.models import VOXMessage`
  instead of the package root. `VOXMessage` is already on the public surface;
  switching the import is an orchestration-side change and is deferred here.