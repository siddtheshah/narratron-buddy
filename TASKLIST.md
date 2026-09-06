# Narratron Task List

## Auth, Security & Monetization
- [ ] Require an existing deployment/owner record before deploying or deleting a theater, rather than treating a missing record as authorized.
- [ ] Add rate limits for registration/login, password reset, join-key resolution, uploads, payment attempts, and Live WebSocket connections.
- [ ] Use secure production auth cookies and CSRF protection for authenticated state-changing endpoints.
- [ ] Require authenticated theater ownership (or the active baton holder where appropriate) for agent start, stop, and status endpoints.
  - The current lifecycle endpoints accept a theater ID without checking the caller's identity or authorization.
- [ ] Cloudflare


## Refactors
- [ ] Refactor canvas.html to be more modular.
- [ ] Refactor canvas_state and split responsibilities.
- [ ] Refactor the tools to use only required parameters in construction.
- [ ] Refactor image cycle into VisualState

## Quality
- [ ] In animation technique selection, avoid video if there is any reference attached.

## Performance
- [ ] Add database and request observability before and after optimization.
  - Record per-endpoint request count, latency, DB query count/time, live-pool checkout waits/timeouts, and cache hit rate.
  - Establish load-test baselines for canvas, OBS, and popout viewers; report DB reads per active viewer and verify the WebSocket migration materially reduces them.

## Policy Pages
- [ ] Page for terms of use.
- [ ] Page for privacy policy.

## Adventure Editor
- [ ] Bring lightweight adventure runner to narratron main site from testlab.

## New Demos
- [ ] Drawing demo

## Billing
- [ ] Storage Daemon is not checking file sizes of owned theaters. Need to fix.
