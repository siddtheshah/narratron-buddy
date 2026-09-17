# Narratron Task List

## Auth, Security & Monetization
- [ ] Add rate limits for registration/login, password reset, join-key resolution, uploads, payment attempts, and Live WebSocket connections.
- [ ] Use secure production auth cookies and CSRF protection for authenticated state-changing endpoints.
- [ ] Require authenticated theater ownership (or the active baton holder where appropriate) for agent start, stop, and status endpoints.
  - The current lifecycle endpoints accept a theater ID without checking the caller's identity or authorization.
- [ ] Cloudflare

## Quality Testing
- [ ] Test Deepseek V4.1-flash as an alternative to gemini.
   - Can use LiteLLM as a wrapper to get compatibility with ADK agents in story modules.
- [ ] Find better voicing models
   - Gemini TTS is very good, but extremely expensive per turn. 


## Refactors
- [ ] Eliminate traces of 'named element' terminology.
- [ ] Refactor canvas.html to be more modular.

## Performance
- [ ] Add database and request observability before and after optimization.
  - Record per-endpoint request count, latency, DB query count/time, live-pool checkout waits/timeouts, and cache hit rate.
  - Establish load-test baselines for canvas, OBS, and popout viewers; report DB reads per active viewer and verify the WebSocket migration materially reduces them.

## Billing
- [ ] Storage Daemon is not checking file sizes of owned theaters. Need to fix.

## Adventure Mode
- [ ] Achievements; let adventure writers come up with accomplishments for players to achieve.

## Adventures
- [ ] Escape room adventure: for hardcore puzzlers. More constrained, but with a freeform hinter. 
- [ ] The Judge adventure: psychological thriller. Players are continually confronted by Death over the choices
they make. 