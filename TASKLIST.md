# Narratron Task List

## Auth, Security & Monetization
- [ ] Add rate limits for registration/login, password reset, join-key resolution, uploads, payment attempts, and Live WebSocket connections.
- [ ] Use secure production auth cookies and CSRF protection for authenticated state-changing endpoints.
- [ ] Require authenticated theater ownership (or the active baton holder where appropriate) for agent start, stop, and status endpoints.
  - The current lifecycle endpoints accept a theater ID without checking the caller's identity or authorization.
- [ ] Cloudflare


## Refactors
- [ ] Refactor canvas.html to be more modular.
- [x] Refactor image cycle into VisualState

## Text Input
- [ ] Add configurability to hotkey. Currently has a default input but should be modifiable.

## Docs
- [ ] Add Instructions for Text Input
- [ ] Add instructions for adventure development. 
  - [ ] We're going to make Anti-gravity our primary environment for adventure development, with testlab as its runner. Add instructions for downloading the git repo and setting up.


## Quality
- [ ] In animation technique selection, avoid video if there is any reference attached.
- [ ] Improve sticky note handling
   - [ ] Idea 1: Sticky note updates be a secondary model call after scene reaction is returned. Reduces load on primary scene reaction, improves consolidation.
   - [ ] Idea 2: Hidden sticky notes. Add an option in canvas to view hidden sticky notes. Set configuration option to hide them.

## Performance
- [ ] Add database and request observability before and after optimization.s
  - Record per-endpoint request count, latency, DB query count/time, live-pool checkout waits/timeouts, and cache hit rate.
  - Establish load-test baselines for canvas, OBS, and popout viewers; report DB reads per active viewer and verify the WebSocket migration materially reduces them.

## Policy Pages
- [ ] Page for terms of use.
- [ ] Page for privacy policy.

## New Demos
- [ ] Drawing demo

## Billing
- [ ] Storage Daemon is not checking file sizes of owned theaters. Need to fix.
