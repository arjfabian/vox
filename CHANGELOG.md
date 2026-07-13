# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> **Note**: No API stability guarantees are implied — VOX remains pre-1.0.

## [0.4.0] - 2026-07-13

This release consolidates a security and stability hardening pass across the agent lifecycle, the persistent asset store, and the HTTP control surface, alongside new capabilities and an architectural cleanup of the runtime layer.

### Security
- **API Server Authentication:** `api_server.py` now requires a bearer token (`VOX_API_TOKEN`) for all lifecycle-mutating routes, compared using `hmac.compare_digest` to avoid timing side-channels. The server refuses to bind to any non-loopback host unless a token is set, and logs a prominent warning when running in unauthenticated dev mode (loopback-only).
- **Path Traversal Fix:** Hardened agent sandboxing in `VOXAgent.get_safe_path` by using `Path.is_relative_to` against the resolved sandbox root instead of a naive string-prefix check, closing a sibling-directory escape vector (`assets/` vs `assets_evil/`).
- **Cryptographic Salt Derivation:** `AgentVault` now generates and persists a random per-vault salt in a dedicated `vault_meta` table instead of deriving it from the non-secret agent ID, removing cross-vault correlation risks. Salt creation is race-safe using `INSERT OR IGNORE` and re-selection to prevent concurrent first-boots from overwriting key derivation material.
- **Fail-Fast Vault Validation:** Verification of `VOX_MASTER_KEY` presence now occurs before any vault file is allocated on disk, preventing orphaned `secrets.vault` files when keys are missing.

### Fixed
- **Agent Registry Concurrency:** Resolved race conditions in the orchestrator (`stop_agent`, `restart_agent`, `start_agent_by_name`) by holding a per-agent `asyncio.Lock` across the entire read-await-mutate sequence, preventing `KeyError` exceptions and inconsistent states between registries.
- **Asset Store TOCTOU Race:** Converted duplicate checksum verification and insertion in `VOXAgentStore.store_file` into a single atomic transaction leveraging a `UNIQUE` constraint and `IntegrityError` handling.
- **Unchecked Write Failures:** Fixed an issue where `store_file` discarded database insertion results, which previously caused files to be orphaned on disk if the SQL execution failed.
- **Deterministic Delete Ordering:** Modified `delete_file` to drop the database row before unlinking the asset from disk, preventing stale index entries if the database deletion fails.
- **Idempotent Data Migration:** Added a safe, rollback-able database migration in `_init_db` to automatically detect and deduplicate legacy pre-constraint rows (keeping the earliest `archived_at` entry) without crashing during construction.

### Changed
- **Capability Loading:** Switched dynamic loading from ad-hoc `importlib.util` specs to native `importlib.import_module`, removing manual `sys.modules` bookkeeping.
- **Explicit Core Contracts:** Promoted `VOXOrchestrator._get_capability_instance` to the public method `get_capability_instance`.
- **Degraded State Tracking:** Introduced the `degraded_agents` registry to isolate and report agents that fail their boot sequences instead of leaving them in ambiguous operational states.
- **Hardened API Surface:** Replaced the legacy `services/api_server/` package with the secure `vox/api_server.py` module, adding authenticated `POST` routes for lifecycle control (`pause`, `resume`, `stop`, `start`, `restart`).
- **Strict Runtime Requirements:** `VOXRuntime.api_server` is now a mandatory field; initializing a runtime without an API server now triggers a construction error.
- **State Transition Validation:** Enforced deterministic state machine rules by validating assignments via `AgentState.can_transition_to` on every mutation.
- **Parameter Validation Lifecycle:** Moved `VOXCapability.PARAMS` validation from mount-time to an explicit `initialize()` execution on `VOXBoundCapability`.

### Added
- **Core Integrations:** Added native capabilities for `Ollama` (`capabilities/ai/ollama`), browser automation (`capabilities/net/browser` via Playwright), and local voice-to-text processing (`capabilities/comm/voicetotext` via `faster-whisper`).
- **Public Security APIs:** Exported `AgentVault` from `vox.security` as part of the framework's public interface.
- **Centralized Initialization:** Added `runtime/factory.py:build_vox()` to unify orchestrator and API server assembly.

### Removed
- **Unused Dispatch Paths:** Removed the legacy dynamic tool-dispatch methods `VOXCapability.run()` and `VOXBoundCapability.run()`.
- **Legacy Orchestrator Gates:** Removed `VOXOrchestrator._check_capability_gate`.
- **Leaked Accessors:** Removed `VOXRole.get_event_map()`, replacing it with private `_handlers` access inside the core runtime package.
