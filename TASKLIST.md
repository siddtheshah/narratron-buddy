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

## Adventure Mode
- [ ] Set theme.
- [ ] Give narratron more visibility into characters so that the characters can "talk".
- [ ] Give narratron a character speech tool, which overlays a speech bubble. 
- [ ] Or perhaps, we let Narratron hand over the user's response to the script tool and have it generate the reaction based on the script.
- [ ] Need billing on text provider for adventure mode. It'll rack up too.

## Music Cost & Variants
- [ ] Add a lower-cost music-variant workflow: generate a base track once, reuse it by default, and use local loop/crossfade/tempo/EQ transformations where they preserve quality.
- [ ] Evaluate an audio-to-audio music editor for substantive variants (for example, extensions or mood/section changes) and add a provider adapter that accepts a source track plus an edit instruction.
  - Lyria 3 does not currently support iterative editing of its generated clips, so this requires a separate provider.
  - Record source generation and variant costs separately before setting a variant credit rate.

## Observability Tool
- [ ] Let agent pull observability if it wants. Reset auto-observabiiity interval when it does, give it some cooldown.

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
