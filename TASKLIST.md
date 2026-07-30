# Narratron Task List

## App Deployment & Infrastructure
- [x] Storage daemon (standalone process in storage/ folder; auto-removes non-persistent theaters older than 7 days and accrues charges for persistent ones)

## Auth, Security & Monetization
- [ ] Determine fair pricing
  - Need to track number of image calls used.
  - Need to track number of voice minutes.
  - Need to track size of total theaters that are stored serverside.
- [ ] Require theater-owner authorization for save, export-assets, deploy, and delete actions; never allow a logged-in non-owner to access or mutate another theater.
- [ ] Require an existing deployment/owner record before deploying or deleting a theater, rather than treating a missing record as authorized.
- [ ] Replace simulated credit-card handling with a payment provider's hosted checkout and verified webhook before enabling real purchases.
- [ ] Add rate limits for registration/login, password reset, join-key resolution, uploads, payment attempts, and Live WebSocket connections.
- [ ] Use secure production auth cookies and CSRF protection for authenticated state-changing endpoints.
- [ ] Review public theater listing, stats, and configuration endpoints; expose only intentionally public metadata.
- [ ] Cloudflare

## Theaterization
- [x] Narratron Theater -> "Theater"
  - Rather than allow confusion between ADK theaters and the narratron theater, we relabel it to theater.
  - This enables Theater : Agent : Canvas correspondence, decouples the agent audio websocket from the specific user.
  - Will need to migrate the database schema.


## Narratron UI & Canvas Features

- [ ] Deep animation pipeline. 
  - Maybe only for pro-canvases. Will require around 4-5 LLM calls to make it, long latency.
  - Requires creating multiple images with an effective alpha layer. Basic case would be background + foreground elements.
  - Also would require an emplacer function. 
  - Allow specification of relative animation, e.g.  a character moving across a background.
- [ ] Revise existing image
  - If the image is on canvas, it'll be faded in. 
  - Otherwise, the adapted image will simply remain available to be pulled in.

## Bugs

- [ ] Unresponsive to voice input. Potentially sequence state updates to be after a viewer takes a pause.
- [x] Move the old mic binding to bind the connect button instead.
- [x] Deletion of theaters/theaters does not work!