# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> **Note**: No API stability guarantees are implied — VOX remains pre-1.0.

## [0.5.4] - 2026.08.12

### Added
- **`AGENTS.md`** — a repository-local instruction file for LLM coding agents (Claude Code, Codex, OpenCode and similar). It carries the working rules an agent needs to avoid architectural drift, and also records test conventions, the Conventional Commits / release ritual, and a conservative done checklist. The file deliberately stays short and operational — it is agent guidance, not project documentation; load-bearing invariants remain enforced by the test suite rather than by prose.

### Removed
- **`WHATSAPP-SETUP.md`** — the setup guide for the WhatsApp Cloud API channel (Meta credentials, VOX config, tunnel, webhook verification, curl smoke test) now lives in the Wiki; this repository no longer carries it.

## [0.5.3] - 2026.08.11

### Added
- **WhatsApp Cloud API adapter (`WhatsAppAdapter`)** — a second `comm.gateway` provider beside Telegram, built on the same adapter contract. Declares `CHANNEL="whatsapp"`, `WEBHOOK_PATH="/webhook/whatsapp"`, its own `PARAMS` (`WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_API_VERSION`, default `v25.0`) and `SENSITIVE_PARAMS` for vault injection. Implements inbound webhook parsing (`messages[]` text/image/audio/document/video plus `statuses[]`), Meta `X-Hub-Signature-256` HMAC-SHA256 verification, the `hub.challenge` subscription handshake, and outbound text messages via `POST /{version}/{phone_id}/messages` using an `httpx.AsyncClient`.
- **Real Meta webhook compatibility fixtures** — three webhook payloads captured from the WhatsApp Cloud API / Graph API v25.0 test environment, preserved under `tests/fixtures/whatsapp/` (`inbound_text.json`, `status_sent.json`, `status_delivered.json`) as permanent regression fixtures. The observed v25.0 payload topology is preserved exactly; all real identifiers (names, phone numbers, WABA/phone-number/app/conversation/message IDs, timestamps, opaque `internal_1p_only_data` values) were replaced with deterministic synthetic placeholders, and a regression guard (`test_fixtures_contain_no_real_captured_identifiers`) fails the suite if any captured value returns. Compatibility surface documented as: WhatsApp Cloud API v25.0 — tested payload shapes: inbound text message, outbound status: sent, outbound status: delivered.
- **`TestWhatsAppCompatibilityFixtures`** — fixture-driven tests proving captured (sanitized) v25.0 payload shapes normalize into the provider-agnostic `VOXInboundMessage` contract (channel, message_id, sender_id, content_type, text, `sender_metadata["profile_name"]`) and that provider-specific data remains reachable via `raw_payload` rather than leaking into the generic contract. Includes an outbound test asserting the exact Graph API request shape (`messaging_product`, `recipient_type`, `to`, `type`, `text.body`) against `POST /v25.0/{PHONE_NUMBER_ID}/messages`, using the existing `httpx.MockTransport` pattern.
- **`TestWhatsAppAdapter`** — unit suite for the new adapter: text/status/image/malformed/unknown parsing, HMAC verification (valid, wrong, missing header, non-`sha256=` prefix), `hub.challenge` handshake (valid, wrong token, wrong mode), and outbound send (success, failure, unconfigured).
- **`TestIngressServerAbstraction`** — contract tests proving the abstraction holds for arbitrary providers: a `test_provider` adapter (custom channel + webhook path) and a `no_webhook_provider` adapter (no webhook path) parse inbound, pass lifecycle start/shutdown, and register/omit webhook routes correctly.
- **`WHATSAPP-SETUP.md`** — step-by-step setup guide (Meta credentials, VOX config, tunnel, webhook verification, curl smoke test).

### Changed
- **`ADAPTER_REGISTRY` extended** — now maps three channels (`telegram`, `webhook`, `whatsapp`); gateway `PARAMS`/`SENSITIVE_PARAMS` aggregate over the registry and `capability.yml` holds only gateway-owned params (`GATEWAY_PORT`, `GATEWAY_HOST`).
- **`tests/test_comm_gateway.py`** — grown from 28 to 48 tests covering all three adapters, generic server routing, body-signing, handshakes, outbound mocking, adapter-contract abstraction, the sanitized compatibility fixtures, and the fixture privacy guards.
- **Agent roles** (per-agent `chat`/`buyer` role modules) — call `comm.gateway.send_text("telegram", TELEGRAM_USER_ID, text)` explicitly now that `send_broadcast()` no longer exists.

### Removed
- **`CommGatewayCapability.send_broadcast()`** — the last provider-specific method in the gateway core (hardcoded `channel="telegram"` plus `TELEGRAM_USER_ID`). Removed per the provider-decoupling refactor; callers must pass the channel and recipient explicitly to `send_text()`.

### Fixed
- **WhatsApp credentials no longer degrade unconfigured agents** — `WHATSAPP_ACCESS_TOKEN` and `WHATSAPP_PHONE_NUMBER_ID` now default to the empty "unconfigured" sentinel (`""`) instead of `None` (the "required" marker in `VOXBoundCapability.validate_params()`). Because `comm.gateway` is a system capability mounted on every agent, the previously required-marked params caused any agent not running WhatsApp (e.g. Telegram-only agents) to be flagged `DEGRADED` at bootstrap. The WhatsApp channel now activates only when both credentials are present, matching `WhatsAppAdapter.is_configured()` semantics and the vault-injection flow.
- **`TestIngressServerAbstraction` helper adapters renamed** — `TestProviderAdapter` / `TestProviderAdapterNoWebhook` are stub adapters, not test classes; their `Test*` names made pytest attempt collection and emit a `PytestCollectionWarning` (class has `__init__`). Renamed to `_ProviderAdapter` / `_ProviderAdapterNoWebhook` to exclude them from the default collection pattern.

## [0.5.2] - 2026.08.11

### Added
- **`BaseAdapter.WEBHOOK_PATH`** — adapters now declare their own inbound HTTP route (e.g. Telegram `"/webhook/telegram"`, generic `"/webhook/generic"`). The shared `IngressServer` registers channels from these paths, so a new provider (WhatsApp, Slack, Discord, …) plugs in with **zero server changes**.
- **`BaseAdapter.handle_verification(query)`** — optional provider GET subscription handshake hook. Returns the challenge string to echo (e.g. Meta's `hub.challenge`) or `None` to reject with 403.
- **`verify_request(request, body)`** — the inbound verifier now receives the raw, unparsed request body, enabling body-signing providers (Meta's `X-Hub-Signature-256` HMAC-SHA256 over the exact payload bytes).
- **`IngressServer._make_verification_handler()`** — per-channel GET handshake routes that echo the raw challenge as plain text (not JSON), as Meta and other providers require.
- **`TestIngressServerGenericRoutes`** — new test class covering: generic route registration from adapter-declared `WEBHOOK_PATH`, Meta-style body-signature verification (valid HMAC dispatches, forged signature returns 403), and the `hub.challenge` echo handshake (valid token echoes, mismatch returns 403).

### Changed
- **`comm.gateway` fully decoupled from the Telegram adapter** — `IngressServer._register_routes()` no longer contains any `telegram`/`webhook` branches; it iterates the mounted adapters and registers `POST` (payload) and `GET` (handshake or generic "registered" response) from each adapter's `WEBHOOK_PATH`. This is the final step of the `comm.telegram → comm.gateway` migration: the gateway core now has no channel-specific knowledge whatsoever.
- **Body-first inbound verification** — the webhook handler reads the raw request body before verifying, then passes it to `verify_request(request, body)`. aiohttp caches the payload, so the subsequent `request.json()` is unaffected; header-token channels (Telegram, generic webhook) simply ignore the new argument.
- **`TelegramAdapter` / `WebhookAdapter`** — declare their own `WEBHOOK_PATH` and accept the optional `body` parameter on `verify_request` (unused by header-token channels).

## [0.5.1] - 2026-08-10

### Added
- **Import of `CommandInfo` in `VOXAgent`** — `vox.agents.base` now imports and re-exports `CommandInfo` from `vox.roles`, fixing an F821 (undefined name) on `get_command_map()`.
- **Import of `Any` in `TelegramAdapter` and `VOXOrchestrator`** — `Any` was referenced but never imported; added to `typing` imports in `gateway/adapters/telegram.py` and `orchestration/base.py`.
- **`VOXForensicLogger` forward reference in `VOXCapability`** — Added under `TYPE_CHECKING` in `capabilities/base.py` to resolve the F821 on `logger: "VOXForensicLogger"`.
- **`tests/test_comm_gateway.py`** — new test suite for the `comm.gateway` long-polling path: inbound `getUpdates` payload normalisation (`text` key) and outbound `sendMessage` formatting.

### Changed
- **Type annotation modernisation** — Replaced legacy `typing.Dict`, `typing.List`, `typing.Optional`, and `typing.Callable` with built-in generics (`dict`, `list`, `X | None`) and `collections.abc.Callable` across all source and test files. Removed unused `importlib` import from `agents/base.py`.
- **Implicit `Optional` fixed** — `args: list[str] = None` in `test_runtime.py` corrected to `args: list[str] | None = None` (PEP 484).
- **Import sorting cleaned** — Alphabetised imports in `agents/__init__.py`, `agents/base.py`, `api_server.py`, `orchestration/base.py` (I001). `runtime/__init__.py` intentionally preserved — `VOXRuntime` must import before `daemon` to avoid circular dependency.
- **`__all__` sorted** — `agents/__init__.py` `__all__` list alphabetised (RUF022).
- **Lint-clean codebase** — All actionable ruff errors resolved: redundant `%s` in `logger.exception()` calls (TRY401), unparenthesized implicit string concatenations (ISC004), unused variables prefixed with `_` (RUF059), nested `with` statements combined (SIM117), unused `cap` assignment removed (F841), `isinstance` calls merged and nested `if`s flattened in `ast_analyzer.py` (SIM101), stale `global` declaration cleaned (PLW0602), `time.sleep` replaced with `asyncio.sleep` in async tests (ASYNC251), `.keys()` removed from `key in dict` checks (SIM118), import sorting cleaned (I001), `__all__` sorted (RUF022).
- **Unused exception bindings removed** — `except Exception as e:` where `e` was never referenced changed to `except Exception:` in `memory.py` and `store.py` (F841).
- **Telegram long-polling restored in `comm.gateway`** — the `TelegramAdapter` now runs a `getUpdates` polling loop (in addition to the webhook server), feeding parsed `VOXInboundMessage`s through the same dispatch path. Replaces the old `comm.telegram` inbound transport, which was webhook-only after the migration and required an HTTPS-reachable `setWebhook` registration to receive anything. Roles consume the normalized payload via the `text` key. The HTTP client read timeout (long-poll + 5s buffer) now exceeds the long-poll hold, eliminating the empty-`ReadTimeout` "Telegram poll error" spam on every quiet poll.
- **`TELEGRAM_LONG_TIMEOUT` configurable with fail-fast guard** — the `getUpdates` long-poll timeout is now a `comm.gateway` param (default 25s); the client read timeout is derived from it (+5s buffer) so the two can never silently drift apart, and `TelegramAdapter` validates at construction that the client timeout exceeds the long-poll timeout, failing the agent boot with a clear message instead of error-spamming on an incompatible configuration.

### Fixed
- **`inspect_vault.py` column detection** — the secrets table is now inspected against the actual vault schema: the ciphertext column lookup previously checked for `ciphertext`/`value` and fell back to an out-of-range index (`columns[3]`), crashing on the real `encrypted_value` column. It now detects `encrypted_value` first and the fallback index is corrected to `columns[2]`, so the tool reports encrypted records instead of erroring.
- **Import order circular dependency** — Reverted `runtime/__init__.py` and `agents/__init__.py` import reorderings from the v0.5.0 refactor that broke circular import chains (e.g. `vox.runtime` → `vox.runtime.daemon` → `vox.runtime.control_plane` → `vox.runtime`).
- **`AgentProvisionError` re-export** — Restored `AgentProvisionError` in `agents/base.py` import from `agents/loader.py` (dropped during v0.5.0 import cleanup), fixing `test_agents_base.py` collection error.

### Infrastructure
- **By-design lint suppressions** — Added `# noqa` comments with rationale to all intentional patterns: `BLE001` (defensive catches at module boundaries), `S110`/`S112` (best-effort cleanup and resilient iteration), `B017` (proxy tests asserting broad exceptions), `ASYNC230` (small file reads in async context), `DTZ006` (local-time log formatting), `TC004` (type-only import kept under `TYPE_CHECKING` to avoid circular import), `RUF012` (mutable class attributes for per-agent capability overrides — `ClassVar` rejected because it prevents instance-level param binding), `F401` (re-exported `AgentProvisionError`), `I001` (import order preserved to avoid circular dependency in `runtime/__init__.py`).

## [0.5.0] - 2026.07.25

### Added
- **Pub/Sub War Room** — `VOXWarRoom` (async incident queue) and `VOXWarRoomMaster` (reactive dispatcher) in `src/vox/orchestration/war_room.py`. Alerts are fanned out to all active agents via `agent.emit("on_war_room_alert", ...)` and mirrored to an external messenger channel — no polling.
- **`comm.gateway` capability** — domain-agnostic multi-channel gateway with `IngressServer` (shared singleton, ref-counted lifecycle), adapter-driven outbound (`TelegramAdapter`, `WebhookAdapter`), and `send_broadcast()` convenience for agents. Replaces both `comm.telegram` and `comm.messenger`.
- **`VOXOrchestrator.panic_shutdown()`** — synchronous emergency stop that cancels agent tasks, purges `AgentVault._key` from all agents, and flushes the war room queue. Triggered on core-compromise detection.
- **Alert routing** — `dispatch_inbound_message()` now detects `payload["type"] == "alert"` and routes to the war room instead of agent-to-agent delivery.
- 27 new tests covering `VOXWarRoom`, `VOXWarRoomMaster`, all three messenger connectors, factory resolution, panic shutdown, and alert routing.
- `VaultAccessError` exception class in `vox.security.vault` — a dedicated, catchable exception for vault initialisation failures.
- Fail-fast vault check in `CapabilityBinder.inject_vault_secrets()`: when `VOX_MASTER_KEY` is unset and a capability's `SENSITIVE_PARAMS` values are not provided via config/`.env`, a `VaultAccessError` is raised immediately, transitioning the agent to `FAILED` state with a clear error message. Previously the missing key was silently swallowed and only manifested as "zero operative agents" at the fleet level.
- `test_boot_fails_when_vault_missing_with_sensitive_params` and `test_boot_succeeds_without_vault_when_no_sensitive_params` in `tests/test_agents_base.py` covering both fail-fast and graceful-degradation paths.
- Support for asynchronous SQLite storage via `aiosqlite` in `VOXAgentStore` (`memory.db`) and `VOXAgentMemory` (`logs.db`).
- `init_db()` method on `VOXAgentStore` and `VOXAgentMemory` to decouple object instantiation from database connection / schema creation.
- `asyncio_mode = "auto"` and `asyncio_default_fixture_loop_scope = "function"` to `pyproject.toml` for pytest-asyncio integration.
- `async init_db()` on `SemanticCache` and `AgentVault` — deferred schema creation for the three remaining modules.
- `SemanticCache._initialized` auto-heal flag: `lookup()`/`store()`/etc. self-initialize on first use if `init_db()` was never called.
- `AgentVault.get_sync()` — synchronous `get()` variant for the bootstrap path, sharing the same `_decrypt()` helper as the async `get()`.

### Changed
- **`comm.messenger` → `comm.gateway` migration:** Deprecated `comm.messenger` capability deleted. All agents now mount the `comm.gateway` capability via `VOXAgent._system_capabilities`. `VOXWarRoomMaster` accepts a plain-text broadcast callback instead of `MessengerBase`. Agent roles reference `comm.gateway` directly.
- **Async persistence migration:** `VOXAgentStore` and `VOXAgentMemory` fully migrated from synchronous `sqlite3` to `aiosqlite`. All public methods (`query()`, `execute()`, `store_file()`, `retrieve_file()`, `delete_file()`, `search_files()`, `record()`, `get_recent()`, `get_thread()`, `get_pending()`) are now `async def` and must be called with `await`.
- `VOXAgent.boot()` now calls `await self.store.init_db()` and `await self.memory.init_db()` during the boot cycle, initialising the agent's private databases asynchronously before capability boot.
- Migrated `tests/test_agents_store.py` and `tests/test_agents_memory.py` from `unittest.TestCase` (synchronous) to async `pytest` tests using `pytest-asyncio`.
- Concurrency test in `test_agents_store.py` migrated from `concurrent.futures.ThreadPoolExecutor` to `asyncio.gather`.
- Mock-based error-path tests updated to patch `aiosqlite.connect` (via `AsyncMock` and custom `_RaisingResult` helper) instead of `sqlite3.connect`.
- **RAG retriever** (`rag.py`): `RAGRetriever.retrieve()`, `_fts_search()`, and `_asset_search()` fully migrated from `sqlite3` to `aiosqlite`.
- **Semantic cache** (`cache.py`): `SemanticCache._init_db()`, `_query_one()`, and `_execute()` migrated to async `aiosqlite`; DB schema creation deferred from `__init__` to `async init_db()`.
- **Agent vault** (`vault.py`): `AgentVault.get()`, `set()`, `list_inactive()`, `list_active()`, `disable()`, `activate()` migrated to async `aiosqlite`. `__init__` retains sync `sqlite3 as _sync_sqlite3` for one-time bootstrap (schema creation + salt derivation). `inject_vault_secrets()` in `capability_binder.py` is now `async def`; `_inject_vault_for_capability()` uses the sync `get_sync()` variant.
- **Test migrations:** `test_llm_rag.py`, `test_llm_cache.py`, `test_security_vault.py` converted from `unittest.TestCase` to async `pytest`.
- **RuntimeWarning fix:** Changed `mock_conn` from `AsyncMock` to `MagicMock` in three error-path tests (`test_agents_store.py`) — `MagicMock` is compatible with `async with` because it returns objects directly (not coroutines) and has built-in `__aenter__`/`__aexit__`.
- **sqlite3 ResourceWarning fix:** Replaced `with sqlite3.connect(...) as conn:` pattern with explicit `try/finally` + `conn.close()` in `vault.py` and `test_agents_store.py` helpers — `sqlite3.Connection.__exit__` does not close the connection, causing unclosed-database warnings under Python 3.14.
- **VOXOrchestrator decomposition:** Monolithic `src/vox/orchestration/base.py` refactored into four single-responsibility modules:
  - `src/vox/orchestration/registry.py` — `VOXRegistry`: capability discovery, capability loading, manifest scanning, agent discovery
  - `src/vox/orchestration/graph.py` — `AgentGraph`: agent hierarchy resolution, ID lookups, children traversal
  - `src/vox/orchestration/controller.py` — `FleetController`: per-agent lifecycle operations (stop, restart, start, pause, resume) with lock-guarded state transitions
  - `src/vox/orchestration/base.py` — `VOXOrchestrator` reduced to a facade that wires the three services together and owns boot/shutdown/dispatch
- `_discover_capabilities()`, `_load_capability()`, `get_capability_instance()`, `_scan_agent_manifests()`, `_discover_agents()`, and `_hire_agent()` moved to `VOXRegistry`.
- `_all_agents`, `resolve_agent_id()`, `get_hierarchy_snapshot()`, `get_children()`, and `_resolve_agent()` moved to `AgentGraph`.
- `stop_agent()`, `restart_agent()`, `start_agent_by_name()`, `pause_agent()`, and `resume_agent()` moved to `FleetController`.
- Public dicts (`active_agents`, `inactive_agents`, `degraded_agents`, `capability_registry`) remain on `VOXOrchestrator` for backward compatibility.
- `CapabilityEntry` re-exported from `vox.orchestration` for existing importers.
- **VOXAgent decomposition:** Monolithic `src/vox/agents/base.py` (592 lines) refactored into four single-responsibility modules:
  - `src/vox/agents/loader.py` — `AgentLoader`: manifest parsing, schema validation, identity checks
  - `src/vox/agents/ast_analyzer.py` — `ASTAgentAnalyzer`: static AST scanning for capability discovery
  - `src/vox/agents/capability_binder.py` — `CapabilityBinder`: capability mounting, vault injection, param validation, role disabling
  - `src/vox/agents/base.py` — `VOXAgent` reduced to lifecycle, event routing, and rate limiting
- `_load_manifest()`, `_validate_manifest()`, `_load_env()`, `_validate_identity()`, `MANIFEST_SCHEMA`, and `AgentProvisionError` moved to `AgentLoader`.
- `_scan_role_capabilities()` and `_is_capabilities_chain()` moved to `ASTAgentAnalyzer`.
- `_discover_and_mount_capabilities()`, `_init_vault()`, `_init_vault_sync()`, `_inject_vault_for_capability()`, `_inject_vault_secrets()`, `_disable_roles_with_missing_capabilities()`, and `_disable_roles_for_capability()` moved to `CapabilityBinder`.
- `AgentLoader`, `AgentProvisionError`, `ASTAgentAnalyzer`, and `CapabilityBinder` exported from `vox.agents.__init__`.

### Fixed
- **Vault capability-name migration** — Agent vaults provisioned under `comm.messenger` (pre-v0.5.0) are automatically migrated to `comm.gateway` on first access. Both `_ensure_db_sync()` (sync path) and `init_db()` (async path) detect `comm.messenger` rows in the `secrets` table and rename them to `comm.gateway`. Idempotent — no-op when no old rows exist.
- **Vault provisioning optional-param prompting** — `provision_vault.py` now marks optional sensitive params (non-`None` defaults like `GATEWAY_WEBHOOK_SECRET`, `TELEGRAM_WEBHOOK_SECRET`) with `[OPTIONAL]` and allows blank input to skip them. Required params (`TELEGRAM_BOT_TOKEN`) still enforce non-empty input.
- **Broadcast delivery:** `CommGatewayCapability.send_broadcast()` now passes the channel ID to `TelegramAdapter`, enabling correct outbound routing instead of falling through to the stdout dev fallback.
- **Telegram message formatting:** `TelegramAdapter.send_outbound()` uses a smart heuristic — when `payload.text` is provided the value is sent as clean plain text; otherwise the structured alert format (`🔔 Alert: type …`) is used. Ordinary greetings and chat replies no longer render with an alert banner.
- **Tools scripts async compatibility:** `tools/provision_vault.py` — all `AgentVault` calls (`list_active`, `list_inactive`, `set`, `activate`, `disable`) were being invoked synchronously but are `async` in `vault.py`. Converted `_sync_vault` and `main` to `async def` and wrapped the entry point in `asyncio.run()`.
- **Tools purge table name:** `tools/inspect_vault.py` — purge query referenced a table `vault_secrets` that does not exist in the vault schema (actual name: `secrets`).
- Eliminated blocking synchronous I/O overhead on `memory.db` and `logs.db` from within agent async execution loops.
- Test suite no longer calls async methods without `await` (30 previously broken tests now pass).
- **3 RuntimeWarnings** resolved: unawaited `AsyncMock` coroutines in error-path mock tests eliminated by using `MagicMock` for connection mocks (incompatible with `async with` protocol).
- **sqlite3 ResourceWarnings:** Closed all `sqlite3.Connection` handles explicitly via `try/finally` + `conn.close()` in `vault.py` and test helpers, preventing unclosed-database warnings under Python 3.14+.

### Removed
- **Deprecated `comm.telegram` capability** — entire `src/vox/capabilities/comm/telegram/` directory deleted. Replaced by `comm.gateway` with adapter-driven architecture.
- **Deprecated `comm.messenger` capability** — entire `src/vox/capabilities/comm/messenger/` directory deleted. All agents and the war room now use `comm.gateway` exclusively.

## [0.4.5] - 2026.07.14

### Security
- Native security gateway/WAF in `src/vox/security/guardrails.py` using signature detection (`InputSanitizer` with 9 dangerous patterns — SQLi, XSS, command injection, path traversal, code execution).
- Global inbound security checks for all external communication vectors (`VOXOrchestrator.dispatch_inbound_message`).
- HTTP-level input sanitization middleware on POST endpoints (`VOXAPIServer._guardrail_middleware`).
- Sliding-window rate limiter (`RateLimiter`) in `src/vox/security/rate_limiter.py`.
- Per-agent rate-limit enforcement in `VOXAgent.emit()` — before any event dispatch, the agent checks the rate limiter; on breach a CRITICAL alert is logged and the emission is dropped.
- `agent.rate_limiter_utilization` property exposing current window utilization as percentage.
- Rate-limit telemetry in `agent.describe()` output (`rate_limiter_utilization_pct`).
- Manifest config keys `rate_limit_max_calls` (default: 30) and `rate_limit_window` (default: 60s) for per-agent tuning.

### Added
- System-wide automatic injection of `comm.messenger` capability via `VOXAgent._system_capabilities`.
- Core-level Telegram Operator identity gate mapping via `VOXAgent.GLOBAL_AGENT_KEYS` and `global_env` injection from root environment into each agent's config.
- `FleetMessenger` singleton (`src/vox/services/fleet_messenger.py`) for broadcasting operational alerts to the War Room channel; wired into orchestrator init/shutdown.
- `WAR_ROOM_ID` configuration parameter for War Room target identity (`VOXConfig.war_room_id`).
- Vault-aware capability validation: vault init moved before capability discovery (`_init_vault_sync()`), per-capability vault secret injection (`_inject_vault_for_capability()`) before `validate_params()`.
- Parameter validation lifecycle on `VOXBoundCapability` (`validate_params()`) with degraded-state fallback when required params are missing.
- `tools/provision_vault.py` sys.path fix for agent role import resolution + `FLEET_MANDATORY` list for fleet-wide vault provisioning.
- Asynchronous agent file watcher (`AgentFileWatcher`) in `src/vox/orchestration/watcher.py`.
- SHA-256 based polling of `agent.yml` and `roles/**/*.py` (excluding `_test.py` and `__pycache__`); detected changes trigger a hot-restart of the affected agent.
- `restart_agent()` rewritten as a full isolated restart: graceful shutdown → re-hire from disk (fresh manifest, roles, vault) → boot → ACTIVE or DEGRADED; does not disrupt other agents.
- Watcher runs automatically on orchestrator boot; can be disabled via `VOX_WATCH_DISABLED=true` environment variable.

### Changed
- `VOXAgent._bootstrap()` reordered: vault initialized before capabilities discovered and mounted; global environment merged before identity validation.
- Tests updated to provide `comm.telegram` mock for agents that depend on it during boot, and to verify degraded-state routing on missing params.

### Fixed
- `_discover_agents._can_create()` now treats `None` `master_id` as empty string, fixing a latent bug where agents without `master_id` in their manifest were never onboarded.

### Cleaned
- Deprecated legacy `input_sanitizer.py` utility completely removed.

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
