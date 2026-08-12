# Narratron Task List

## Auth, Security & Monetization
- [ ] Require an existing deployment/owner record before deploying or deleting a theater, rather than treating a missing record as authorized.
- [ ] Add rate limits for registration/login, password reset, join-key resolution, uploads, payment attempts, and Live WebSocket connections.
- [ ] Use secure production auth cookies and CSRF protection for authenticated state-changing endpoints.
- [ ] Require authenticated theater ownership (or the active baton holder where appropriate) for agent start, stop, and status endpoints.
  - The current lifecycle endpoints accept a theater ID without checking the caller's identity or authorization.
- [ ] Cloudflare

## Narratron UI & Canvas Features

- [ ] Deep animation pipeline. 
  - Maybe only for pro-canvases. Will require around 4-5 LLM calls to make it, long latency.
  - Requires creating multiple images with an effective alpha layer. Basic case would be background + foreground elements.
  - Also would require an emplacer function. 
  - Allow specification of relative animation, e.g.  a character moving across a background.
- [ ] Revise existing image
  - If the image is on canvas, it'll be faded in. 
  - Otherwise, the adapted image will simply remain available to be pulled in
- [ ] Improve Gleam. Have it apply contrast instead of a pure brightening.

## Refactors

- [ ] Refactor canvas.html to be more modular.
- [ ] Refactor database definitions to live as an external SQL schema, not baked in python code.
    - Important if postgres sql will become a necessary migration.

## User Profiles
- [ ] Implement proper account deletion.
    - Create script to delete user data from DB and Cloud Storage.
    - Consider a soft-delete approach for auditability if needed, but user-facing must be complete removal.
    - Purge any theaters and assets the user has.

## Performance
- [ ] Add server-side authentication and theater-access caching.
  - Cache validated sessions by a cryptographic hash of the auth token, never the raw token.
  - Cache valid sessions for a bounded 30–60 second TTL, capped at the session expiry; negative-cache invalid tokens for about 5 seconds.
  - Add request-local memoization so repeated `get_current_user()` calls in one request never cause duplicate database queries.
  - Cache `(principal, theater_id)` access grants separately, including join-key/cookie viewers; auth caching alone does not reduce anonymous viewer access checks.
  - Invalidate affected session/account cache entries on logout, password reset, profile updates, credit changes, and microphone-sensitivity updates.
  - Use Redis/Memorystore for shared cache state if the app runs more than one instance; a bounded in-process TTL cache is acceptable for a single instance.
  - Instrument cache hits, misses, evictions, and stale-account invalidations before tuning TTLs.
- [ ] Deduplicate frontend auth-state requests.
  - Provide one shared `getAuthState()` promise/cache in `static/js/auth-flow.js`.
  - Canvas should reuse it for chat identity, baton controls, and microphone sensitivity rather than making three `/api/auth/me` calls during initial load.
  - Explicit auth events (login, logout, registration, account updates) must invalidate or refresh the browser cache.
- [ ] Replace high-frequency canvas REST polling with WebSocket state updates.
  - The canvas currently polls latest state and chat every second, plus suggestions every two seconds; authenticated viewers therefore create about five DB reads per second from access checks alone.
  - Use a separate notification-only canvas-state WebSocket (not the doodle protocol) to publish revisioned invalidations for latest image/agent activity, chat, and suggestions.
  - Keep REST as the authoritative initial-hydration/reconnect fetch; use slow, background-aware fallback polling only while the notification socket is disconnected.
  - Add version/ETag responses and idempotent client application for conditional, coalesced refreshes.
- [ ] Collapse redundant database work in theater and baton APIs.
  - Refactor `GET /api/theaters/{id}` so access validation, current-user resolution, and deployment lookup share results instead of querying them again.
  - Replace theater-list per-theater metadata/deployment queries with a joined/batched query; avoid the current N+1 pattern.
  - Replace baton-state's per-user lookups with one batched/joined query for owner, active orator, and allowed orators.
  - Avoid synchronous database operations in async request handlers where they can block the event loop.
- [ ] Add and verify database indexes for real query patterns.
  - Normalize join keys (or add a supported expression index) so join-key resolution does not scan `canvas_deployments` through `UPPER(join_key)`.
  - Normalize username/email on write (or use functional indexes) so case-insensitive login does not scan `users`.
  - Add indexes for `theater_views(theater_id, viewed_at DESC)`, `canvas_deployments(user_id)`, `payment_transactions(user_id, id DESC)`, `auth_sessions(user_id)`, and time-based statistics queries as appropriate.
  - Add migration tests and `EXPLAIN QUERY PLAN`/Turso plan verification for each index-backed query.
- [ ] Add database and request observability before and after optimization.
  - Record per-endpoint request count, latency, DB query count/time, live-pool checkout waits/timeouts, and cache hit rate.
  - Establish load-test baselines for canvas, OBS, and popout viewers; report DB reads per active viewer and verify the WebSocket migration materially reduces them.

## Policy Pages
- [ ] Page for terms of use.
- [ ] Page for privacy policy.

## Major bugs


- [ ] Agent reconnect requires an additional stop/start cycle.
