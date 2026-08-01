# Narratron Task List

## Auth, Security & Monetization
- [ ] Require theater-owner authorization for save, export-assets, deploy, and delete actions; never allow a logged-in non-owner to access or mutate another theater.
- [ ] Require an existing deployment/owner record before deploying or deleting a theater, rather than treating a missing record as authorized.
- [ ] Replace simulated credit-card handling with a payment provider's hosted checkout and verified webhook before enabling real purchases.
- [ ] Add rate limits for registration/login, password reset, join-key resolution, uploads, payment attempts, and Live WebSocket connections.
- [ ] Use secure production auth cookies and CSRF protection for authenticated state-changing endpoints.
- [ ] Review public theater listing, stats, and configuration endpoints; expose only intentionally public metadata.
- [ ] Cloudflare

## Narratron UI & Canvas Features

- [ ] Deep animation pipeline. 
  - Maybe only for pro-canvases. Will require around 4-5 LLM calls to make it, long latency.
  - Requires creating multiple images with an effective alpha layer. Basic case would be background + foreground elements.
  - Also would require an emplacer function. 
  - Allow specification of relative animation, e.g.  a character moving across a background.
- [ ] Revise existing image
  - If the image is on canvas, it'll be faded in. 
  - Otherwise, the adapted image will simply remain available to be pulled in.

## Refactors

- [ ] Refactor web_viewer_app into api_server folder.
- [ ] Refactor deployer/ out of existence, move logic.

## Long Context

- [ ] Agent appears to be reading files in the background, possibly, and going astray.
   - Profile usage of the LoadArtifactsTool. Figure out what's really happening.

## Image Effects

- [ ] Improve gleam a little more. Have it apply contrast instead of a pure brightening.