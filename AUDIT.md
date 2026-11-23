# Project audit — open findings

Originated from the **2026-10-19** security and architecture audit; cleaned up on **2026-11-22** to remove resolved findings (S01–S06, S10, S12, S14, S17) and retain only items still open in the codebase.

Priorities reflect this application's exposure and the stated prerequisites, rather than a formal CVSS assessment: **P1** means address promptly; **P2** means schedule a targeted fix; **P3** means maintenance.

## Open security and correctness findings

### S07 — [P1] Scene-suggestion refresh permits unlimited unmetered model requests

**Evidence: Reproduced with the model client mocked.** An anonymous viewer with a join key issued two `refresh=true` requests for a zero-credit theater. Both invoked the model; the owner's balance remained zero.

**Code:** `api_server/theaters.py` (`get_theater_suggestions`), `services/suggestion_service.py` (`generate_suggestions`).

**Cause and impact:** canvas access permits generation, and a caller-controlled `refresh` query flag bypasses caching. There is no model-call budget, refresh cooldown, concurrent-request limit, or credit admission on this path. A free account can create a theater and repeatedly consume the operator's model quota. The synchronous model request also occupies the event loop.

**Patch scope:** separate reading cached suggestions from requesting generation; constrain refresh authority, deduplicate concurrent work, and impose shared per-principal/theater/global budgets. Explicitly decide whether generation is sponsored or billed and enforce that policy.

**Regression check:** repeated or concurrent refreshes, including anonymous join-key access and zero-credit accounts, cannot create unbounded provider calls.

### S08 — [P2] Passwords use a single fast SHA-256 operation

**Evidence: Source-confirmed.** Registration, login, password reset, and the local seed account all use `sha256(password + salt)`.

**Code:** `storage/database.py` (user registration, login verification, password reset, and local seed account).

**Impact:** salts prevent a shared precomputed lookup table, but do not make each password guess expensive. A stolen user table permits fast offline guessing.

**Patch scope:** introduce a versioned password-hashing service using a password-specific KDF such as Argon2id or scrypt. Rehash legacy credentials after successful login and use the new format for registration/reset; keep all four paths consistent.

**Regression check:** legacy users can log in and migrate; new/reset passwords use the new verifier; invalid credentials never migrate an account.

### S09 — [P2] Password-reset tokens are not consumed atomically and sibling links survive a reset

**Evidence: Both behaviors reproduced.** Two coordinated requests using the same token both succeeded. Separately, a second outstanding reset link remained valid after the first link changed the password and could change it again.

**Code:** `storage/database.py` (`password_reset_tokens` lookup and `UPDATE password_reset_tokens SET used = TRUE WHERE token = ?`).

**Cause and impact:** validation and token consumption occur in separate transactions. Consumption has no conditional unused/unexpired check, and successful reset marks only that particular token used. This weakens single-use guarantees and leaves previously issued recovery links able to override the recovered password.

**Patch scope:** atomically claim an unused, unexpired token and change the password in one transaction, checking the affected row count. Invalidate all outstanding reset tokens for that user when reset succeeds, alongside sessions.

**Regression check:** only one of two concurrent uses succeeds, and every earlier recovery link fails after a successful reset. Include a real PostgreSQL concurrency test.

### S11 — [P2] Generation admission does not reserve credits; slot allocation also races across workers

**Evidence: Credit race reproduced; slot race source-confirmed/conditional.** Two editor jobs in different drafts, funded for only one generation, both passed admission and completed. The final balance was minus one generation's price.

**Code:** `api_server/theater_editor.py` (`generate_draft_asset`), `services/generation_billing.py`, `storage/database.py` (`acquire_generation_slot`).

**Cause and impact:** the billing lock is released while the provider runs, with no reservation, so both jobs see the same available balance. Process-local locks do not coordinate multiple instances. Generation slots independently perform a count followed by insert without locking a common owner row; PostgreSQL transactions can both observe capacity and over-admit. This is distinct from the database's documented policy allowing already-incurred usage to make a balance negative.

**Patch scope:** atomically reserve an owner's spending allowance before work, settle once on success, and release on failure/cancellation. Serialize slot admission in the database and renew leases for jobs that outlive their TTL.

**Regression check:** simultaneous jobs cannot reuse one admission balance or exceed the slot cap, including independent PostgreSQL connections and multiple application instances.

### S13 — [P2] Suggestion votes and withdrawals trust client-supplied identities

**Evidence: Reproduced.** One anonymous viewer holding a join key cast votes as three invented voters, then withdrew the authenticated owner's suggestion by naming that owner.

**Code:** `api_server/canvas.py` (`upvote_suggestion`, `withdraw_suggestion`), `components/canvas/chat_manager.py`.

**Impact:** suggestion ranking can be forged and other participants' submissions removed. The accepted top suggestion can influence the paid narrator session.

**Patch scope:** derive voter and author identity on the server from an authenticated account or a server-issued anonymous participant identity. Authorize withdrawal against that identity, with explicit owner moderation if desired; separate display names from identity keys.

**Regression check:** one participant cannot vote as another identity, multiply votes by changing names, or remove another participant's suggestion.

### S15 — [P2] Join capabilities are short, and sensitive endpoints lack shared abuse limits

**Evidence: Source-confirmed.** Normal join keys contain six characters from a 32-character alphabet (30 bits). The anonymous resolver accepts repeated guesses; login and forgot-password also have no application-level throttling. Unlike these paths, user-help already has an explicit limiter.

**Code:** `components/theater_manager.py` (`TheaterMetadata.join_key`), `api_server/theater_editor.py`, `api_server/theaters.py`, `api_server/auth.py`, `services/user_help_limits.py`.

**Impact:** short, long-lived bearer codes provide a comparatively small guessing space; the global resolver can discover any matching theater. Login guessing and reset-email flooding are likewise limited only by infrastructure/provider controls.

**Patch scope:** use high-entropy bearer capabilities for links; if short human-entry codes remain, make them scoped, short-lived, collision-checked, and rate-limited. Add distributed principal/address/global limits to login, reset, and join resolution with bounded tracking and reliable client-address handling.

**Regression check:** limits hold across workers, rotations revoke old capabilities, and duplicate short codes cannot bind two theaters.

### S16 — [P2] Account deletion and theater redeployment have remaining lifecycle and ownership gaps

**Status: Partially resolved.** Theater reset (`reset_theater_draft`) now stops the live session and calls `canvas_states.reset(theater_id)`, and theater deletion (`destroy_theater`) now stops the live session, removes local files, deletes the durable repository package, and evicts cached canvas state.

**Remaining gaps:**
- `delete_my_account` (`api_server/profiles.py`) deletes the database user row and cascading records without stopping active sessions or cleaning up local/durable theater files and drafts.
- `deploy_existing_theater` (`api_server/theaters.py`) still reconstructs from durable storage and assigns ownership (`record_deployment`) to the caller when no deployment row exists. Any retained durable package without a deployment row remains reclaimable by another authenticated account that knows its ID.

**Patch scope:** apply explicit lifecycle cleanup on account deletion (quiesce sessions/workers and apply the intended retention policy to durable packages and drafts). Never reconstruct or reassign ownership when a deployment row is absent; persist ownership/tombstones independently of removable deployment rows.

**Regression check:** account deletion cleans up or quarantines owned live/durable theaters as documented, and another account cannot redeploy a retained package without ownership.

## Valuable refactorings

### R01 — [P2] Give theater documents a single writer and atomic persistence

**Evidence: Source-confirmed, concurrency-dependent.** `CanvasStateManager.save_local_theater_data` reads/modifies/truncates `theater.json` directly, while `TheaterManager._save_metadata` writes the same file independently. Background generation, exports, WebSocket changes, and sync request workers can all reach persistence. `CanvasStateService.get` also creates state without synchronization.

**Code:** `components/canvas/canvas_state_manager.py`, `components/theater_manager.py`, `components/canvas/canvas_state_service.py`, `api_server/theaters.py`.

**Value:** prevent lost metadata/state, partial JSON after interruption, duplicate coordinators on concurrent cold access, and inconsistent repository snapshots.

**Patch scope:** centralize per-theater state ownership and mutation, write through unique temporary files with atomic replacement, and coordinate snapshot/export revisions. Multi-instance storage needs cross-process coordination or transactional/versioned records; a process-local lock alone is insufficient.

**Verification:** deterministic competing metadata/state writes and concurrent initialization preserve both updates; an interrupted write leaves the previous complete document readable.

### R02 — [P2] Move blocking database/model/filesystem work off the async event loop

**Evidence: Source-confirmed.** Async lifecycle/status handlers issue synchronous database queries; the async suggestion endpoint performs a synchronous model call; stamp upload decodes/encodes images inline. Live-session refresh and usage settlement also perform synchronous database operations from asynchronous streaming paths.

**Code:** `api_server/app.py`, `api_server/theaters.py`, `api_server/profiles.py`, `services/live_agent_manager.py`.

**Value:** one slow query, model response, or image operation currently stalls other HTTP/WebSocket activity on that worker. Database timeouts bound duration but do not prevent event-loop blockage.

**Patch scope:** expose consistent async service operations, use async SDK calls where supported, and offload blocking work through bounded executors. Serialize shared state transitions separately from I/O.

**Verification:** a deliberately delayed DB/provider operation does not delay an unrelated heartbeat or WebSocket response; cancellation and executor capacity remain bounded.

### R03 — [P2] Persist usage obligations and reconcile failed settlements

**Evidence: Source-confirmed, failure-dependent.** Failed live-usage writes retain batches only in `LiveAgentSession._pending_usage_batches`. Session removal/process loss can discard them; generation callbacks and downstream events share the batching state without an explicit settlement lock. Editor billing failures may delete an asset even when a timed-out commit actually succeeded.

**Code:** `services/live_agent_manager.py`, `api_server/theater_editor.py`.

**Value:** existing event keys prevent duplicate settlement retries, but do not make obligations durable or resolve unknown commit outcomes. Failures can leave unpaid provider usage or a charged user without the generated asset.

**Patch scope:** introduce durable generation/usage job records with stable event keys and pending/settled/failed states. Serialize batch creation/settlement, reconcile by event key after uncertain commits, and retain unsettled obligations across session shutdown/restart. Couple reservations from S11 to this workflow.

**Verification:** failures before/after commit, shutdown during retry, and concurrent completion callbacks produce exactly one charge and a recoverable asset or explicit refund.

### R04 — [P3] Replace duplicated transport/access plumbing and runtime method patching with explicit contracts

**Status: Partially resolved.** `RegistryDependency` (`api_server/dependencies.py`) has been removed and explicit constructor dependency injection has been rolled out across core services.

**Remaining scope:** route modules still duplicate notepad discovery and WebSocket broadcast logic; `_enable_state_notifications` (`api_server/canvas.py`) still patches a state/connection method at runtime; and `storage/database.py` still combines authentication, payments, moderation, theater lifecycle, and generation admission in one large class.

**Patch scope:** extract focused repositories/services incrementally, centralize canvas authorization and transport registration/broadcasting, and give each peer a bounded outbound queue and send timeout so one stalled send cannot delay acknowledgement indefinitely.

**Verification:** run endpoint security tests through actual authorization dependencies, with contract-complete mocks only at external boundaries. Keep SQLite unit tests, and add isolated PostgreSQL integration tests for transaction races and SQL semantics (`storage/postgresql.py` is a SQLite translation adapter, not a PostgreSQL server).

## Suggested patch order

1. **S07**: bound unmetered scene-suggestion generation.
2. **S16**: prevent ownership takeover via `deploy_existing_theater` and close account-deletion lifecycle cleanup.
3. **S08, S09, S11, S13, S15**: patch independently with the listed regression checks.
4. **R01–R04**: follow-up changes to strengthen persistence, concurrency, settlement, and service contracts.
