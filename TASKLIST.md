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
- [x] Tri-frame animation
  - We generate a trio of frames that chain together to form a loopable animation. 
- [ ] Adventure Mode
  - Let narratron do some more active decision making by generating its own story elements as an option.
  - We can use a text generation model to build out a script ahead of the current state and adjust it
  - continuously based on what the user is giving back.
  - Long term planning will likely be very bad, but maybe we can let the script writer query some docs the user
  has shared or added to the assets.
- [ ] Music Generation
  - Need to find a model provider and assess latency. Probably have a testlab page for it.
  - Music needs to be saved to output/music and be referenceable by the live agent just like images are.

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
- [ ] Add database and request observability before and after optimization.
  - Record per-endpoint request count, latency, DB query count/time, live-pool checkout waits/timeouts, and cache hit rate.
  - Establish load-test baselines for canvas, OBS, and popout viewers; report DB reads per active viewer and verify the WebSocket migration materially reduces them.

## Policy Pages
- [ ] Page for terms of use.
- [ ] Page for privacy policy.
