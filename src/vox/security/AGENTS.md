# AGENTS.md — security silo

Normative working rules for the VOX security layer in `src/vox/security/`.
This is agent instruction, not project documentation. It defines the **target
architecture**: the boundary the security layer must converge on, not a record
of every current internal. Existing code that conflicts with the boundary is
explicitly labeled NON-CONFORMANT and to be closed, never treated as accepted
architecture.

This contract is checked against the sibling AGENTS.md documents in
`capabilities`, `workloads`, `orchestration`, `runtime`, and `config`; where
those contracts place secret/vault lifecycle ownership, they are treated as
established architecture.

## Responsibility

The security silo owns the **security primitives and their policy/enforcement
logic** — the classes and algorithms, plus the security-relevant state and
mutation inside them. It is a **library/leaf silo**, producing services that
other silos host and invoke. It does **not** own where those services are
instantiated or where policy is applied to *specific* messages/fleet events;
that invocation/routing belongs to the owning silo (typically orchestration for
inbound messaging, workloads for per-workload event emission and the workload
vault).

Owned here:

- **Guardrail policy and enforcement** — `InputSanitizer` (dangerous-pattern
  detection + HTML escaping) and `SecurityError`. The *enforcement invocation*
  (call sanitize, decide drop-vs-route) is owned by the routing silo.
- **Rate limiting** — `RateLimiter` (sliding-window) and `RateLimitError`. The
  class and its internal counters are owned here; the *enforcement site* (which
  workload, which outbound emit) is owned by the workload silo.
- **Speaker/identity profiles** — `VOXSpeakerProfile` (voice-embedding load and
  cosine-similarity verification). The class here; the *identity directory* is
  supplied by the hosting silo (orchestration).
- **Secret/key storage and lifecycle** — `WorkloadVault` (AES-256-GCM, PBKDF2,
  per-workload SQLite) and `VaultAccessError`. The class, crypto, and key
  lifecycle (`_key` derivation/destruction) are owned here. The *hosting and
  per-workload lifecycle* (construction in the binder, `purge_vault` teardown)
  is owned by the workload silo per `workloads/AGENTS.md`.
- **Security state and mutation** — state lives inside the primitives
  (`RateLimiter._calls`, `WorkloadVault._key`, `VOXSpeakerProfile._embedding`)
  and is mutated only through public methods (`allow`/`check_limit`,
  `get`/`set`/`disable`/`activate`/`wipe`, `load`/`verify`). Consumers never
  touch these privates.

## Modules

- `__init__.py` — public package surface. Exports the primitives consumers use
  across silos.
- `guardrails.py` — `InputSanitizer`, `SecurityError`.
- `rate_limiter.py` — `RateLimiter`, `RateLimitError`.
- `speaker_profile.py` — `VOXSpeakerProfile`.
- `vault.py` — `WorkloadVault`, `VaultAccessError`.

## Boundaries

### Out of the silo (imports/leaves)
The security silo is a leaf consumer: it imports only `vox.observability`
(logging). It must **not** import or reach into workload, orchestration, API
server, runtime, or capability internals. It exposes behavior to those silos via
the public exports and public methods only.

### Secret storage is a library, secret lifecycle is workload-owned
`WorkloadVault` provides the storage/encryption and its own key lifecycle. The
workload silo owns the *per-workload* instance: the `CapabilityBinder`
constructs it, `VOXWorkload._vault` hosts it, `purge_vault()` destroys it, and
bound capabilities receive secrets via the binder. Do not duplicate vault
construction or lifecycle in the security silo, and do not let consumers reach
`vault._key` — secret destruction must flow through workload-owned
`purge_vault()`/`wipe()`, never by touching the private.

### Guardrail is a policy primitive; routing applies it
`InputSanitizer`/`SecurityError` define *what* is policy-violating. The
*routing policy* — calling `sanitize` and folding `SecurityError` into a
drop/reject decision for a specific ingress (inbound message dispatch, API POST
bodies) — lives in the owning silo (orchestration). Do not move routing policy
into the security silo.

## Architectural invariants (target)

1. The security silo imports only `vox.observability` and stdlib/third-party
   crypto/scientific libs; it never imports workload, orchestration, api,
   runtime, or capability internals.
2. Consumers (workloads, orchestration, API server, provider) use the security
   primitives through the public exports and public methods — never by reaching
   private state (`_key`, `_calls`, `_embedding`, `_identity_dir`).
3. Vault hosting, construction, and per-workload teardown are workload-owned;
   the security silo provides the `WorkloadVault` class and its crypto/key
   lifecycle only.
4. Guardrail enforcement is applied at the routing layer (orchestration), not
   re-implemented or duplicated in the security silo or in workloads.
5. Secret material is held only in `WorkloadVault` and bound capability
   `_secrets`; never in source, tests, logs, or workflows.
6. The security silo does not own workload-local or fleet-level routing,
   configuration source, or capability contracts.

## What constitutes an architectural change

Any of the following must be proposed and reviewed as an architectural change,
not a routine edit:

- adding/changing the public security exports or a primitive's public method
  signature;
- changing the crypto, key-derivation, or storage format in `WorkloadVault`;
- relocating guardrail/rate-limit/speaker/vault *class* ownership, or the
  routing site that invokes a security primitive;
- reaching private security state from outside the silo.

## Testing / architecture enforcement

- `pytest` (`python -m pytest`, asyncio auto mode). Behavior changes ship with
  tests in `tests/test_*.py`.
- `tests/test_security_arch.py` guards the silo: no out-of-silo imports, the
  public-exports surface, and no private-security-attribute reads from other
  silos.
- `tests/test_security.py`, `tests/test_security_vault.py`,
  `tests/test_rate_limiter.py` guard primitive behavior.

## Known boundary debt (current vs. target)

- **Closed — API server now builds its own `InputSanitizer` from `vox.security`.**
  The previously NON-CONFORMANT reach by `src/vox/api_server.py::_guardrail_middleware`
  into the orchestrator's **private** guardrail (`self._orc._guardrail.sanitize`)
  was resolved on the api-server side: `VOXAPIServer` constructs its own
  `InputSanitizer` from the public `vox.security` surface (the security-silo
  recommended fix) and applies it to its HTTP POST ingress. The security silo's
  guardrail policy and enforcement remain owned here; the API server is only the
  transport/invocation boundary. (See also `orchestration/AGENTS.md`.)
- **Reported — keeper `VaultAccessError` not exported from the package root.**
  `WorkloadVault` is exported via `vox.security`, but its companion
  `VaultAccessError` (raised by the vault and caught by the binder) is imported
  only via the deep path `vox.security.vault` in `workloads/capability_binder.py`
  and `workloads/base.py`. The public surface is inconsistent. Closing it is a
  normalization churn that crosses the workload silo (consumer imports), so it is
  deferred/reported rather than imposed here.
- **Reported — `VOXSpeakerProfile.identity_dir` is a `Path` at runtime and was
  mis-annotated `str`.** This was corrected in-silo (annotation now `Path`, as
  orchestration supplies a `Path` and the body uses path division).
- **Note (not a violation)** — `capabilities/ai/llm/sanitizer.py` is a
  capability-internal *prompt/token* sanitizer, functionally distinct from the
  security-silo WAF `InputSanitizer` (prompt hygiene/trimming vs. dangerous-
  pattern WAF). It is correctly co-located with the LLM capability, not a
  duplicated security primitive.