# Project audit — 2026-10-19

Reviewed on **October 19, 2026 (America/New_York)** against commit `962f19b`.

This report replaces the previous audit. It records suspected issues and refactoring opportunities in the current code; **no application fixes have been made**. Findings are separated so they can be patched and verified individually.

## Scope and evidence

Reviewed HTTP and WebSocket authorization, browser rendering, account recovery, credit settlement, uploads/imports, theater lifecycle, persistence, dependencies, and service boundaries. Verification used isolated local databases and temporary files, in-process ASGI requests, offline browser pages, and mocked external providers. No production accounts, email deliveries, payments, or paid model calls were used. The directory-deletion probe mocked the deletion operation.

Evidence labels:

- **Reproduced:** observed in a focused local probe. Browser execution was verified for the remote and chat injections.
- **Source-confirmed:** the relevant implementation was traced, but the complete exploit or failure was not executed.
- **Conditional:** impact depends on deployment or a particular failure/concurrency scenario.

**P1** means address promptly; **P2** means schedule a targeted fix; **P3** means maintenance. Priorities reflect this application's exposure and the stated prerequisites, rather than a formal CVSS assessment.

## Security and correctness findings

### S01 — [RESOLVED] [P1] Theater deletion accepts a parent-directory path

**Status: Resolved.**

**Evidence: Reproduced.** `DELETE /api/theaters/%2e%2e` reaches the delete handler with `theater_id == ".."`. With an ordinary authenticated account and no corresponding deployment row, the handler calls `shutil.rmtree` for the parent of the theater workspace root. The probe intercepted that call; it deleted no directory.

**Code:** `api_server/theaters.py:1178`, `api_server/theaters.py:1185`, `components/theater_manager.py:534`, `components/theater_manager.py:910`.

**Cause and impact:** ownership is checked only when a deployment record exists, while the manager joins an unvalidated identifier to its base directory. This permits destructive traversal outside a theater, subject to filesystem permissions. Windows backslashes and absolute/drive paths need equivalent rejection.

**Patch scope:** require an existing deployment owned by the caller; validate theater identifiers before any filesystem operation; enforce resolved containment inside the manager and repository themselves. Reject the root and parent directories explicitly.

**Resolution:**
- Implemented `validate_theater_id` in `components/theater_manager.py` to validate single-component paths and reject empty strings, percent-encoded characters, traversal components (`.`, `..`), Windows drives/separators, reserved names, and invalid characters.
- Enforced resolved containment in `TheaterManager._get_theater_dir`, `TheaterManager.destroy_theater`, `TheaterRepository.theater_path`, and `TheaterRepository.delete_theater` with explicit rejection of root, parent, and escaping paths.
- Updated `DELETE /api/theaters/{theater_id}` in `api_server/theaters.py` to validate `theater_id` and require an existing deployment record owned by the authenticated caller prior to invoking deletion.
- Added comprehensive regression tests in `components/theater_manager_test.py`, `storage/theater_repository_test.py`, and `api_server/theaters_test.py` covering encoded dot segments, Windows separators/drives, missing deployment records, symlink escapes, and non-owner access.

**Regression check:** encoded dot segments, Windows separators/drives, missing deployment records, symlink escapes, and another owner's theater must never reach deletion. Verified with automated regression tests.

### S02 — [RESOLVED] [P1] Playlist uploads can host active HTML on the application origin

**Status: Resolved.**

**Evidence: Reproduced.** A signed-in, zero-credit account can upload `active.html` through the `playlist_audit` multipart field of `/api/theaters/create-and-deploy`. An anonymous request with the resulting join key receives the exact file as inline `text/html`, without an attachment disposition or sandbox CSP.

**Code:** `api_server/theaters.py:1081`, `components/theater_manager.py:697`, `components/theater_manager.py:777`, `api_server/theaters.py:524`, `api_server/theaters.py:540`.

**Cause and impact:** direct playlist uploads validate paths but not media extensions/content. The serving route infers MIME type from the uploaded filename. An attacker can share their theater's playlist URL with a logged-in victim and execute script with that victim's application authority. The existing reference-image protections do not cover this route.

**Patch scope:** validate supported audio at every ingestion boundary and serve only approved media types. Quarantine legacy unsupported files as sandboxed downloads or serve untrusted content from a separate origin.

**Resolution:**
- Implemented `playlist_audio_type` and `PLAYLIST_AUDIO_FORMATS` in `components/playlist_audio.py` to validate supported audio extensions (`.mp3`, `.wav`, `.ogg`, `.flac`, `.m4a`, `.aac`), binary container headers/magic bytes, and reject disguised active markup (`<!doctype`, `<html`, `<script`, `<svg`, `<?xml`, etc.).
- Enforced audio validation at every playlist ingestion boundary in `TheaterManager.create_theater`, `TheaterManager.extract_asset_package`, and `create_and_deploy_theater` in `api_server/theaters.py`, rejecting unsupported or disguised files before writing to disk.
- Hardened `serve_theater_playlist_track` in `api_server/theaters.py` to validate media extensions and content before serving inline audio with `X-Content-Type-Options: nosniff` and `Content-Security-Policy: sandbox; default-src 'none'; media-src 'self'`, while quarantining legacy or invalid files on disk as sandboxed `application/octet-stream` downloads (`Content-Disposition: attachment`).
- Added comprehensive regression tests in `components/theater_manager_test.py` and `api_server/theaters_test.py` covering HTML, SVG, and disguised documents across direct, folder, and ZIP upload paths as well as legacy file quarantine on the serving route.

**Regression check:** HTML/SVG and disguised documents submitted through direct, folder, and ZIP paths must never become executable same-origin documents. Verified with automated regression tests.

### S03 — [RESOLVED] [P1] Dice-roll HTML executes in every viewer's chat

**Status: Resolved.**

**Evidence: Reproduced, including browser execution.** A contributor can post `roll_data.html` containing `<img src="/missing" onerror="window.auditRoll=1">`. The API retains it unchanged, and the production chat renderer executes the handler. The popout has the same sink.

**Code:** `api_server/canvas.py:35`, `api_server/canvas.py:829`, `static/js/chat-controller.js:277`, `templates/popout.html:404`.

**Cause and impact:** contributor permission authorizes posting a roll, but the renderer also trusts arbitrary client-supplied markup. A malicious contributor can run script in the owner or another viewer's browser, including authenticated API requests. HttpOnly cookies do not prevent those requests.

**Patch scope:** accept a bounded, typed roll schema and render its values through DOM/text APIs. If imported markup must remain supported, apply a maintained, strict sanitizer at the trust boundary and cover both renderers.

**Resolution:**
- Replaced untyped `roll_data` on `ChatMessage` in `api_server/canvas.py` with a bounded, strictly typed `RollDataPayload` Pydantic schema (`extra="forbid"`, bounded string/list lengths) and `sanitize_roll_html()` validator using `_RollHtmlSanitizer`.
- Stripped dangerous elements/subtrees (`<script>`, `<style>`, `<svg>`, `<math>`, `<iframe>`, `<object>`, `<embed>`, `<form>`, `<img>`, `<a>`, etc.), all `on*` event attributes, and unsafe URL schemes at the API trust boundary.
- Implemented `sanitizeRollHtmlToFragment` using `DOMParser` and explicit DOM node construction (`createElement` / `createTextNode`) with a strict tag and attribute allowlist in both `static/js/chat-controller.js` and `templates/popout.html`.
- Added API and Playwright browser regression tests in `testing/ui/chat_prefix_test.py` and `testing/ui/beyond20_listener_test.py` verifying that event handlers, SVG, scripts, dangerous URL schemes, and oversized payloads are neutralized while legitimate Beyond20 rolls still display.

**Regression check:** event handlers, SVG, dangerous URL schemes, and malformed markup remain inert in canvas and popout, while normal Beyond20 results still display. Verified with automated regression tests.

### S04 — [RESOLVED] [P1] The remote page reflects an unchecked join key into an HTML attribute

**Status: Resolved.**

**Evidence: Reproduced, including browser execution.** For a theater owner, a join-key value of `" onload="window.auditRemote=1" data-unused="` becomes an executable attribute on `<body>`.

**Code:** `api_server/remote.py:65`, `api_server/remote.py:68`, `api_server/shared.py:212`.

**Cause and impact:** owners and active orators are authorized independently of the supplied join key. The remote then interpolates that supplied value without attribute escaping, even when it was never validated. A crafted link targeting a theater the victim owns or controls can execute script on the application origin.

**Patch scope:** render only the authoritative validated key and escape all HTML attributes, preferably through an autoescaping template. Do not interpolate query strings into markup manually.

**Resolution:**
- Updated `read_remote` in `api_server/remote.py` to never reflect unvalidated `join_key` query parameters; only the deployment's authoritative `join_key` is rendered when present and authorized, and `_grant_canvas_access` stores only the validated authoritative key.
- Replaced manual string interpolation of `<body>` attributes in `api_server/remote.py` with an autoescaping Jinja2 template (`_REMOTE_BODY_TEMPLATE = jinja2.Environment(autoescape=True).from_string(...)`).
- Updated `static/js/remote.js` to prefer the server-rendered `document.body.dataset.joinKey` attribute.
- Added regression tests in `api_server/remote_test.py` verifying that owners and active orators opening `/remote` with quotes or markup in `join_key` receive no injected attributes or elements while legitimate access remains usable.

**Regression check:** an owner opening a remote URL with quotes or markup in `join_key` receives no injected elements or event attributes; legitimate access remains usable. Verified with automated regression tests.

### S05 — [P1] Model-controlled image references can read another theater's files

**Evidence: Reproduced through the reference-resolution path.** An absolute path to a test image in a different theater, placed inside a prompt tag, was resolved and returned verbatim as `ImageReference.data`. No provider was contacted.

**Code:** `components/canvas/visual_state.py:198`, `components/canvas/visual_state.py:204`, `components/canvas/visual_state.py:214`, `components/reference_manager.py:2420`, `components/reference_manager.py:2527`, `components/reference_manager.py:2611`, `tools/image/image_tool.py:299`.

**Cause and impact:** `resolve_image_path` accepts any existing filesystem file, and its fallback joins do not consistently enforce resolved containment. Reference preparation reads those bytes and passes them to image generation. An orator who steers a model-generated prompt toward a known server path can cross theater boundaries; arbitrary readable non-image bytes can also reach the outbound provider request, although provider acceptance and attacker recovery of those bytes were not demonstrated.

**Patch scope:** resolve model input only through theater-owned handles. Constrain all candidate and registered paths to explicitly permitted reference/output/capture roots, resolve symlinks, and validate image content before creating provider requests.

**Regression check:** another theater's valid image, a non-image file, absolute paths, traversal, and links escaping the permitted roots are rejected; approved shared assets require explicit registration.

### S06 — [P1] Password-reset URLs trust the request Host header

**Evidence: Reproduced with email dispatch mocked.** A forgot-password request carrying `Host: attacker.example` generated an email link beginning `http://attacker.example/deploy?reset_token=...`.

**Code:** `api_server/auth.py:136`, `api_server/auth.py:145`. A canonical-origin implementation already exists at `api_server/profiles.py:26`.

**Cause and impact:** password recovery constructs its bearer link from `request.base_url`. If the ingress accepts an attacker-selected Host header, a victim following the legitimate reset email sends the token to the attacker's origin. Actual external exploitability depends on proxy/host validation, which was not inspected.

**Patch scope:** use one validated operator-configured public origin for reset and other security-sensitive links; enforce an allowed-host policy at the application or ingress boundary.

**Regression check:** changing Host or forwarded-host inputs never changes the reset-link origin. Incorrect public-origin configuration fails closed.

### S07 — [P1] Scene-suggestion refresh permits unlimited unmetered model requests

**Evidence: Reproduced with the model client mocked.** An anonymous viewer with a join key issued two `refresh=true` requests for a zero-credit theater. Both invoked the model; the owner's balance remained zero.

**Code:** `api_server/theaters.py:1399`, `api_server/theaters.py:1414`, `services/suggestion_service.py:59`, `services/suggestion_service.py:73`, `services/suggestion_service.py:102`.

**Cause and impact:** canvas access permits generation, and a caller-controlled query flag bypasses caching. There is no model-call budget, refresh cooldown, concurrent-request limit, or credit admission on this path. A free account can create a theater and repeatedly consume the operator's model quota. The synchronous model request also occupies the event loop.

**Patch scope:** separate reading cached suggestions from requesting generation; constrain refresh authority, deduplicate concurrent work, and impose shared per-principal/theater/global budgets. Explicitly decide whether generation is sponsored or billed and enforce that policy.

**Regression check:** repeated or concurrent refreshes, including anonymous join-key access and zero-credit accounts, cannot create unbounded provider calls.

### S08 — [P2] Passwords use a single fast SHA-256 operation

**Evidence: Source-confirmed.** Registration, login, password reset, and the local seed account all use `sha256(password + salt)`.

**Code:** `storage/database.py:546`, `storage/database.py:602`, `storage/database.py:1169`, `storage/database.py:2319`.

**Impact:** salts prevent a shared precomputed lookup table, but do not make each password guess expensive. A stolen user table permits fast offline guessing.

**Patch scope:** introduce a versioned password-hashing service using a password-specific KDF such as Argon2id or scrypt. Rehash legacy credentials after successful login and use the new format for registration/reset; keep all four paths consistent.

**Regression check:** legacy users can log in and migrate; new/reset passwords use the new verifier; invalid credentials never migrate an account.

### S09 — [P2] Password-reset tokens are not consumed atomically and sibling links survive a reset

**Evidence: Both behaviors reproduced.** Two coordinated requests using the same token both succeeded. Separately, a second outstanding reset link remained valid after the first link changed the password and could change it again.

**Code:** `storage/database.py:1102`, `storage/database.py:1162`, `storage/database.py:1164`, `storage/database.py:1177`.

**Cause and impact:** validation and token consumption occur in separate transactions. Consumption has no conditional unused/unexpired check, and successful reset marks only that particular token used. This weakens single-use guarantees and leaves previously issued recovery links able to override the recovered password.

**Patch scope:** atomically claim an unused, unexpired token and change the password in one transaction, checking the affected row count. Invalidate all outstanding reset tokens for that user when reset succeeds, alongside sessions.

**Regression check:** only one of two concurrent uses succeeds, and every earlier recovery link fails after a successful reset. Include a real PostgreSQL concurrency test.

### S10 — [P2] SMTP failures expose live reset tokens in logs

**Evidence: Source-confirmed.** SMTP errors fall through to a development fallback that logs and prints the complete reset URL, even when email sending was enabled.

**Code:** `utils/email_service.py:102`, `utils/email_service.py:107`, `utils/email_service.py:110`.

**Impact:** a production mail outage copies recovery credentials and user identifiers into application logs/stdout, broadening access to active reset tokens. The fallback also reports simulated success despite delivery failure.

**Patch scope:** restrict any development token display to an explicit local-only setting; production failures should log redacted diagnostics and never emit the bearer link. Preserve a uniform outward forgot-password response.

**Regression check:** a forced SMTP exception never writes the token to captured logs, stdout, or error messages in production mode.

### S11 — [P2] Generation admission does not reserve credits; slot allocation also races across workers

**Evidence: Credit race reproduced; slot race source-confirmed/conditional.** Two editor jobs in different drafts, funded for only one generation, both passed admission and completed. The final balance was minus one generation's price.

**Code:** `api_server/theater_editor.py:458`, `api_server/theater_editor.py:467`, `api_server/theater_editor.py:481`, `services/generation_billing.py:5`, `storage/database.py:2141`, `storage/database.py:2174`, `storage/database.py:2184`.

**Cause and impact:** the billing lock is released while the provider runs, with no reservation, so both jobs see the same available balance. Process-local locks do not coordinate multiple instances. Generation slots independently perform a count followed by insert without locking a common owner row; PostgreSQL transactions can both observe capacity and over-admit. This is distinct from the database's documented policy allowing already-incurred usage to make a balance negative.

**Patch scope:** atomically reserve an owner's spending allowance before work, settle once on success, and release on failure/cancellation. Serialize slot admission in the database and renew leases for jobs that outlive their TTL.

**Regression check:** simultaneous jobs cannot reuse one admission balance or exceed the slot cap, including independent PostgreSQL connections and multiple application instances.

### S12 — [P2] Upload and import paths can exhaust memory, disk, or transcoding workers

**Evidence: Source-confirmed; no exhaustion load was run.** Theater creation and stamp upload read whole files without application byte caps. Google downloads buffer the response before checking size; the confirmation download bypasses that check, and document export has no equivalent cap. TikTok conversion copies an uncapped upload, launches FFmpeg without a concurrency budget, then reads the whole output into memory.

**Code:** `api_server/theaters.py:1018`, `api_server/profiles.py:176`, `services/google_asset_importer.py:88`, `services/google_asset_importer.py:118`, `services/google_asset_importer.py:128`, `services/google_asset_importer.py:182`, `api_server/tiktok.py:133`, `api_server/tiktok.py:136`, `api_server/tiktok.py:154`.

**Impact:** a low-privilege account can create excessive allocations or persistent storage; TikTok conversion additionally requires a connected TikTok account. A later draft/ZIP size check does not bound the earlier multipart/download allocation. Upstream ingress limits may reduce exposure but were not verified.

**Patch scope:** enforce request, per-file, aggregate, and per-owner storage limits before/between reads; stream downloads with byte limits on every response path; bound decoded image dimensions and video duration/resolution; place transcodes behind a bounded worker queue. Apply equivalent controls to legacy creation and editor imports.

**Regression check:** oversized bodies and streamed responses terminate at the budget, confirmation downloads obey the same cap, and concurrent transcodes are rejected or queued predictably.

### S13 — [P2] Suggestion votes and withdrawals trust client-supplied identities

**Evidence: Reproduced.** One anonymous viewer holding a join key cast votes as three invented voters, then withdrew the authenticated owner's suggestion by naming that owner.

**Code:** `api_server/canvas.py:904`, `api_server/canvas.py:910`, `api_server/canvas.py:917`, `api_server/canvas.py:923`, `components/canvas/chat_manager.py:79`.

**Impact:** suggestion ranking can be forged and other participants' submissions removed. The accepted top suggestion can influence the paid narrator session.

**Patch scope:** derive voter and author identity on the server from an authenticated account or a server-issued anonymous participant identity. Authorize withdrawal against that identity, with explicit owner moderation if desired; separate display names from identity keys.

**Regression check:** one participant cannot vote as another identity, multiply votes by changing names, or remove another participant's suggestion.

### S14 — [P2] Agent status publicly reveals the owner's credit balance

**Evidence: Reproduced.** An unauthenticated request with no join key to `/api/theaters/auditstatus/agent/status` returned the owner's exact test balance of `37.5`.

**Code:** `api_server/app.py:217`, `api_server/app.py:219`, `api_server/app.py:234`.

**Impact:** knowing a theater ID is sufficient to inspect account balance and session activity. The balance is account-wide rather than specific to that theater.

**Patch scope:** require canvas authorization for session status and restrict financial fields to the owner. Keep public health checks separate and free of account data.

**Regression check:** anonymous/unauthorized requests receive no status or financial data, and join-key spectators receive only the fields required to render their view.

### S15 — [P2] Join capabilities are short, and sensitive endpoints lack shared abuse limits

**Evidence: Source-confirmed.** Normal join keys contain six characters from a 32-character alphabet (30 bits). The editor fallback uses six hex digits (24 bits). The anonymous resolver accepts repeated guesses; login and forgot-password also have no application-level throttling. Unlike these paths, user-help already has an explicit limiter.

**Code:** `components/theater_manager.py:111`, `api_server/theater_editor.py:526`, `api_server/theaters.py:587`, `api_server/auth.py:81`, `api_server/auth.py:136`, `services/user_help_limits.py:17`.

**Impact:** short, long-lived bearer codes provide a comparatively small guessing space; the global resolver can discover any matching theater. Login guessing and reset-email flooding are likewise limited only by infrastructure/provider controls, which were not audited. No brute-force attack was attempted.

**Patch scope:** use high-entropy bearer capabilities for links; if short human-entry codes remain, make them scoped, short-lived, collision-checked, and rate-limited. Add distributed principal/address/global limits to login, reset, and join resolution with bounded tracking and reliable client-address handling.

**Regression check:** limits hold across workers, rotations revoke old capabilities, and duplicate short codes cannot bind two theaters.

### S16 — [P2] Reset/deletion workflows do not consistently invalidate live and durable state

**Evidence: Reset resurrection reproduced; deletion consequences source-confirmed.** Reset removes history files but retains the cached `CanvasStateManager`; its next persistence operation recreates the deleted chat/history. The delete endpoint removes local files and the deployment row, but does not stop the live agent or delete the durable repository package. Account deletion only removes the database user and cascading records.

**Code:** `api_server/theater_editor.py:593`, `api_server/theater_editor.py:626`, `components/canvas/canvas_state_service.py:16`, `api_server/theaters.py:1188`, `api_server/profiles.py:109`, `storage/database.py:628`, `storage/theater_repository.py:147`.

**Additional suspected access consequence:** `deploy_existing_theater` reconstructs from durable storage and assigns ownership to the caller when no deployment row exists (`api_server/theaters.py:1142`, `api_server/theaters.py:1166`). A retained package after deletion therefore appears reclaimable by another authenticated account that knows its ID. This takeover sequence was not executed and should be verified early; if confirmed, treat that consequence as P1.

**Patch scope:** introduce explicit reset/delete/account-delete lifecycle operations that quiesce writers and workers, close sockets, clear/rebuild cached state, and apply the intended retention policy to durable packages and drafts. Persist ownership/tombstones independently of a removable deployment row; missing ownership must not authorize reconstruction or reassignment.

**Regression check:** reset history never reappears after later activity/restart; deleted theaters stop running; another account cannot redeploy retained packages; deletion has the documented effect on durable content.

### S17 — [P2] The lockfile retains three upstream aiohttp advisories

**Evidence: Dependency scan and upstream notices confirmed.** The lockfile selects `aiohttp==3.14.3`, pulled in by `cloud-sql-python-connector==1.22.0`.

**Code:** `uv.lock:32`, `uv.lock:657`; the Docker build uses frozen resolution in `Dockerfile`.

The audit reported these advisories, all patched upstream in `3.14.4`:

- [GHSA-5wr8-gcqf-gqf3](https://github.com/aio-libs/aiohttp/security/advisories/GHSA-5wr8-gcqf-gqf3): missing initial server request/header timeout; CVE-2026-111064.
- [GHSA-2g87-pp6c-29x5](https://github.com/aio-libs/aiohttp/security/advisories/GHSA-2g87-pp6c-29x5): resource-exhaustion cases.
- [GHSA-494j-54xm-rwp3](https://github.com/aio-libs/aiohttp/security/advisories/GHSA-494j-54xm-rwp3): Secure shared-cookie handling; CVE-2026-111063.

**Applicability:** the public server here is FastAPI/Uvicorn, and aiohttp is a connector dependency. These results establish vulnerable dependency versions, not a demonstrated inbound aiohttp-server exploit in Narratron. Affected feature reachability still needs assessment.

**Patch scope:** update the lockfile to a compatible patched aiohttp release, rebuild the frozen image, and rerun connector/database tests plus the advisory scan. Add recurring lockfile scanning in CI.

## Valuable refactorings

### R01 — [P2] Give theater documents a single writer and atomic persistence

**Evidence: Source-confirmed, concurrency-dependent.** `CanvasStateManager.save_local_theater_data` reads/modifies/truncates `theater.json` directly, while `TheaterManager._save_metadata` writes the same file independently. Background generation, exports, WebSocket changes, and sync request workers can all reach persistence. `CanvasStateService.get` also creates state without synchronization.

**Code:** `components/canvas/canvas_state_manager.py:121`, `components/canvas/canvas_state_manager.py:129`, `components/theater_manager.py:611`, `components/canvas/canvas_state_service.py:16`, `api_server/theaters.py:167`.

**Value:** prevent lost metadata/state, partial JSON after interruption, duplicate coordinators on concurrent cold access, and inconsistent repository snapshots.

**Patch scope:** centralize per-theater state ownership and mutation, write through unique temporary files with atomic replacement, and coordinate snapshot/export revisions. Multi-instance storage needs cross-process coordination or transactional/versioned records; a process-local lock alone is insufficient.

**Verification:** deterministic competing metadata/state writes and concurrent initialization preserve both updates; an interrupted write leaves the previous complete document readable.

### R02 — [P2] Move blocking database/model/filesystem work off the async event loop

**Evidence: Source-confirmed.** Async lifecycle/status handlers issue synchronous database queries; the async suggestion endpoint performs a synchronous model call; stamp upload decodes/encodes images inline. Live-session refresh and usage settlement also perform synchronous database operations from asynchronous streaming paths.

**Code:** `api_server/app.py:146`, `api_server/app.py:217`, `api_server/theaters.py:1415`, `api_server/profiles.py:158`, `services/live_agent_manager.py:882`, `services/live_agent_manager.py:1208`.

**Value:** one slow query, model response, or image operation currently stalls other HTTP/WebSocket activity on that worker. Database timeouts bound duration but do not prevent event-loop blockage.

**Patch scope:** expose consistent async service operations, use async SDK calls where supported, and offload blocking work through bounded executors. Serialize shared state transitions separately from I/O.

**Verification:** a deliberately delayed DB/provider operation does not delay an unrelated heartbeat or WebSocket response; cancellation and executor capacity remain bounded.

### R03 — [P2] Persist usage obligations and reconcile failed settlements

**Evidence: Source-confirmed, failure-dependent.** Failed live-usage writes retain batches only in `LiveAgentSession._pending_usage_batches`. Session removal/process loss can discard them; generation callbacks and downstream events share the batching state without an explicit settlement lock. Editor billing failures may delete an asset even when a timed-out commit actually succeeded.

**Code:** `services/live_agent_manager.py:141`, `services/live_agent_manager.py:1172`, `services/live_agent_manager.py:1208`, `services/live_agent_manager.py:1318`, `services/live_agent_manager.py:1493`, `api_server/theater_editor.py:481`.

**Value:** existing event keys prevent duplicate settlement retries, but do not make obligations durable or resolve unknown commit outcomes. Failures can leave unpaid provider usage or a charged user without the generated asset.

**Patch scope:** introduce durable generation/usage job records with stable event keys and pending/settled/failed states. Serialize batch creation/settlement, reconcile by event key after uncertain commits, and retain unsettled obligations across session shutdown/restart. Couple reservations from S11 to this workflow.

**Verification:** failures before/after commit, shutdown during retry, and concurrent completion callbacks produce exactly one charge and a recoverable asset or explicit refund.

### R04 — [P3] Replace untyped registry proxies and duplicated transport/access plumbing with explicit contracts

**Evidence: Source-confirmed.** `RegistryDependency` forwards through `Any` and dynamic attribute access. Route modules duplicate notepad discovery and WebSocket broadcast logic; `_enable_state_notifications` replaces a connection object's method at runtime. Database responsibilities include authentication, payments, moderation, theater lifecycle, and generation admission in one large class. These patterns make it harder to enforce the project's typing rules and shared security invariants.

**Code:** `api_server/dependencies.py:15`, `api_server/canvas.py:109`, `api_server/canvas.py:128`, `api_server/canvas.py:146`, `api_server/canvas.py:171`, `components/canvas/connection_state.py:47`, `storage/database.py:246`.

**Patch scope:** introduce typed service protocols and explicit dependency providers that tests override; extract focused repositories/services incrementally. Centralize canvas authorization and transport registration/broadcasting. Give each peer a bounded outbound queue and send timeout so one stalled send cannot delay acknowledgement indefinitely.

**Verification:** run endpoint security tests through actual authorization dependencies, with contract-complete mocks only at external boundaries. Keep SQLite unit tests, but add isolated PostgreSQL integration tests for transaction races and SQL semantics; `storage/postgresql.py` is a SQLite translation adapter, not a PostgreSQL server. Restore the lint baseline and then expand annotation/contract checks incrementally.

## Validation results and limits

- Existing backend suite: `uv run pytest -n 4 api_server components pricing providers services storage tools utils -q` — **1,997 passed, plus 69 subtests passed**. This excludes the full UI/testlab/live evaluation suites.
- Initial focused auth/access/payment/database selection: **177 passed**; these overlap the backend suite and are not additional coverage counts.
- Temporary audit probes: **13 passed**, reproducing S01–S07, both reset-token behaviors in S09, the credit-admission race in S11, S13, S14, and reset-state resurrection in S16. The probe file was removed after the audit; the scenarios above describe regression tests to retain when each fix is implemented.
- `uv run --no-sync ruff check . --output-format concise` — **60 existing violations**. No auto-fixes were applied.
- A frozen, non-development requirements export was scanned with `uv run --no-sync --with pip-audit pip-audit ... --no-deps --disable-pip`: **109 environment-applicable dependencies; three advisories in one package**. This is not a scan of the Docker OS or every platform branch of the universal lockfile. pip-audit's direct `--locked` mode did not recognize `uv.lock`, so the frozen export was used instead.
- Existing tests emitted dependency/deprecation warnings, including a scikit-learn artifact trained with `1.9.0` being loaded by `1.9.1`. Passing tests do not establish PostgreSQL race safety or cover the demonstrated attack paths.
- Production ingress policy, deployment configuration, cloud IAM/storage ACLs, external provider behavior, and production data were not audited. Source-confirmed/conditional findings require targeted verification as they are patched. No claim of a complete vulnerability inventory is made.

## Suggested patch order

1. S01: close the destructive path/ownership gap.
2. S02–S04: remove same-origin script execution paths.
3. S05–S07: contain model file access, secure recovery-link origin, and bound unmetered generation.
4. Verify S16's retained-package ownership consequence early; then address reset/deletion lifecycle.
5. S08–S15 and S17: patch independently with the listed regression checks.
6. R01–R04: use focused follow-up changes to strengthen persistence, concurrency, settlement, and service contracts.
