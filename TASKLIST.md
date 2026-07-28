# Narratron Task List

## App Deployment & Infrastructure
- [ ] Cleanup daemon (triggers on session creation, removes sessions not marked durable and older than 7 days)

## Frontend
- [ ] Proper how-to popup for the orator. Doesn't need to be a separate page.
- [ ] Improve Splash Page Load by loading the images incrementally.

## Auth, Security & Monetization
- [ ] Determine fair pricing
  - Need to track number of image calls used.
  - Need to track number of voice minutes.
  - Need to track size of total sessions that are stored serverside.
- [ ] Require session-owner authorization for save, export-assets, deploy, and delete actions; never allow a logged-in non-owner to access or mutate another session.
- [ ] Require an existing deployment/owner record before deploying or deleting a session, rather than treating a missing record as authorized.
- [ ] Replace simulated credit-card handling with a payment provider's hosted checkout and verified webhook before enabling real purchases.
- [ ] Add rate limits for registration/login, password reset, join-key resolution, uploads, payment attempts, and Live WebSocket connections.
- [ ] Use secure production auth cookies and CSRF protection for authenticated state-changing endpoints.
- [ ] Review public session listing, stats, and configuration endpoints; expose only intentionally public metadata.
- [ ] Cloudflare

## Deployment Features
- [ ] Folder upload.
  - Needs a tutorial popup.

## Agent Responsiveness
- [ ] Show flashing indicator for when the agent is "drawing"
  - Helps user understand when agent is doing stuff.
  - Add an indicator for notes taken too.

## Narratron UI & Canvas Features

- [ ] Deep animation pipeline. 
  - Maybe only for pro-canvases. Will require around 4-5 LLM calls to make it, long latency.
  - Requires creating multiple images with an effective alpha layer. Basic case would be background + foreground elements.
  - Also would require an emplacer function. 
  - Allow specification of relative animation, e.g.  a character moving across a background.
- [ ] Revise existing image
  - If the image is on canvas, it'll be faded in. 
  - Otherwise, the adapted image will simply remain available to be pulled in.
- [ ] Baton passing
  - Co-orators must be identified by authenticated accounts; support `owner`, `co-orator`, and `viewer` roles.
  - One account holds the active baton at a time. Only the baton holder may open the audio/agent-control channel.
  - The owner can pass, revoke, take back, and optionally lock the baton; invalidate the former holder's connection immediately on a handoff.
  - Keep the agent conversation separate from the current speaker: use a stable session-scoped agent identity so a handoff does not start a new conversation.
  - Save a durable handoff snapshot (scene summary, story bible, open threads, current image/music, and recent directives) and inject it when the new holder connects.
  - Persist agent-session continuity outside in-memory process state so handoffs survive reconnects, Cloud Run instance changes, and restarts.
