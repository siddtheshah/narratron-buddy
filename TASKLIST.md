# Narratron Task List

## Auth, Security & Monetization
- [ ] Require an existing deployment/owner record before deploying or deleting a theater, rather than treating a missing record as authorized.
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
  - Otherwise, the adapted image will simply remain available to be pulled in
- [ ] Improve Gleam. Have it apply contrast instead of a pure brightening.

## Refactors

- [ ] Refactor canvas.html to be more modular.
- [ ] Refactor database definitions to live as an external SQL schema, not baked in python code.

## Customization
- [x] Move style.txt into theater.yaml
- [x] Special instructions for the agent in theater.yaml.

## User Profiles
- [ ] Create a user profile page showing lifetime credits used, and theater views. Let them write a bio.
- [ ] Implement proper account deletion.
    - Create script to delete user data from DB and Cloud Storage.
    - Consider a soft-delete approach for auditability if needed, but user-facing must be complete removal.
    - Purge any theaters and assets the user has.

## Performance
- [ ] Speed up page loads and toggles in the canvas.
   - Optimistic updates on UI toggles.
   - Simple time based cache for authorization. Investigate Redis and avoid tech debt here.

## Policy Pages
- [ ] Page for terms of use.
- [ ] Page for privacy policy.