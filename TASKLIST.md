# Narratron Task List

## Auth, Security & Monetization
- [ ] Require an existing deployment/owner record before deploying or deleting a theater, rather than treating a missing record as authorized.
- [ ] Add rate limits for registration/login, password reset, join-key resolution, uploads, payment attempts, and Live WebSocket connections.
- [ ] Use secure production auth cookies and CSRF protection for authenticated state-changing endpoints.
- [ ] Require authenticated theater ownership (or the active baton holder where appropriate) for agent start, stop, and status endpoints.
  - The current lifecycle endpoints accept a theater ID without checking the caller's identity or authorization.
- [ ] Cloudflare

## Narratron UI & Canvas Features

- [ ] Layered animation pipeline. 
  - Maybe only for pro-canvases. Will require around 4-5 LLM calls to make it, long latency.
  - Requires creating multiple images with an effective alpha layer. Basic case would be background + foreground elements.
  - Also would require an emplacer function. 
  - Allow specification of relative animation, e.g.  a character moving across a background.

## Refactors
- [ ] Refactor canvas.html to be more modular.
- [x] Theater repository instead of storing theaters in the database.

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
- [x] Adventure demo

## Billing
- [ ] Storage Daemon is not checking file sizes of owned theaters. Need to fix.
