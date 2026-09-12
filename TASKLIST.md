# Narratron Task List

## Auth, Security & Monetization
- [ ] Add rate limits for registration/login, password reset, join-key resolution, uploads, payment attempts, and Live WebSocket connections.
- [ ] Use secure production auth cookies and CSRF protection for authenticated state-changing endpoints.
- [ ] Require authenticated theater ownership (or the active baton holder where appropriate) for agent start, stop, and status endpoints.
  - The current lifecycle endpoints accept a theater ID without checking the caller's identity or authorization.
- [ ] Cloudflare


## Refactors
- [ ] Fix Deep planner config layout
- [ ] Eliminate traces of 'named element' terminology.
- [ ] Refactor canvas.html to be more modular.

## Performance
- [ ] Add database and request observability before and after optimization.s
  - Record per-endpoint request count, latency, DB query count/time, live-pool checkout waits/timeouts, and cache hit rate.
  - Establish load-test baselines for canvas, OBS, and popout viewers; report DB reads per active viewer and verify the WebSocket migration materially reduces them.

## Policy Pages
- [ ] Page for terms of use.
- [ ] Page for privacy policy.

## New Demos
- [ ] Drawing demo

## Social Sharing & Clip Privacy
- [x] Export a canvas clip up to 30 seconds locally, preserving the visual canvas experience: scene, doodles, image effects, text effects, canvas music, and an opt-in creator microphone track.
  - The browser tab-capture flow includes tab audio only when the creator explicitly enables it in the browser picker. Microphone audio is an explicit, revocable choice in the clip stage and is used only for the local recording.
  - Do not upload or persist the clip, music, or voice on Narratron servers during local export.
  - Open a dedicated `obs.html?clip=1` stage and capture that tab so the exported clip contains only the shareable stage; download it locally for the creator to review.
  - When a TikTok account is connected, read its `max_video_post_duration_sec` capability and cap selectable durations accordingly. Thirty seconds is within TikTok's baseline creator limit.
- [ ] Add draft-only social handoffs; Narratron must never directly publish a post.
  - Focus the first connected flow on TikTok; defer Instagram, X, and other destinations whose web APIs do not create user-editable drafts.
  - Prepare an editable caption and hand the clip to the destination's composer; the user reviews and presses Publish in the destination app.
  - Do not request publishing OAuth scopes merely to open a composer or share a local file.
- [ ] Add TikTok's Upload API as the first connected-account option.
  - Use OAuth only with TikTok's upload scope and its `MEDIA_UPLOAD` mode, never Direct Post; TikTok then sends the creator an inbox notification to finish the draft in its editing flow.
  - Store refresh/access tokens encrypted; never expose provider tokens to the browser.
  - Record only minimal handoff status/audit data; do not retain the source image after TikTok confirms it has ingested it.
- [ ] Build a short-lived media handoff for the TikTok video-draft flow.
  - Prefer TikTok's direct file-upload flow: stream the clip to TikTok's one-time upload URL without public hosting or durable Narratron storage.
  - Transcode browser-recorded WebM to a TikTok-compatible MP4 only when necessary; keep any working file private and delete it immediately after TikTok accepts the upload.
  - Poll or receive provider status until the draft is delivered to the creator's inbox, then delete the source clip; apply a short fallback TTL only for abandoned/failed handoffs and disclose it clearly.
- [ ] Reassess each provider before adding a connected flow: do not use APIs that only offer direct publishing when the product requires a user-finished draft.

## Billing
- [ ] Storage Daemon is not checking file sizes of owned theaters. Need to fix.
