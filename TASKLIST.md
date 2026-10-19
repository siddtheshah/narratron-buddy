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


## Refactors
- [ ] Eliminate traces of 'named element' terminology.
- [ ] Refactor canvas.html to be more modular.

## Billing
- [ ] Storage Daemon is not checking file sizes of owned theaters. Need to fix.

## Narratron Mobile Remote (PWA)

Scope: A dedicated, touch-friendly remote for an existing theater, initially focused on iPhone but usable in other browsers. The phone controls Narratron while a separate device displays the canvas and plays audio. Push-to-talk operates only while the remote is open and visible. Deliver as an ordinary web page first, then add Home Screen/PWA support on the same server.

### 1. Validate audio and two-device operation
- [ ] Prototype phone microphone capture into the existing authenticated agent WebSocket while a separate canvas remains connected.
- [ ] Audit `services/live_stream_service.py` and agent session ownership: opening, reconnecting, or closing the remote must not replace the canvas connection, stop the shared agent, or duplicate agent responses/audio playback.
- [ ] Define microphone ownership when the same active orator has both devices open. Prevent simultaneous desktop and phone transmission; release ownership on disconnect or loss of control.
- [ ] Test the current WebCodecs Opus configuration on a real iPhone. `static/js/audio-recorder.js` currently requires `AudioEncoder`, introduced in Safari 26; check the exact codec configuration rather than API presence alone.
- [ ] Choose and document the minimum supported iOS version. If older iPhones are in scope, implement a compatible capture/encoding path and matching server handling; do not assume MediaRecorder container chunks can replace the existing raw Opus packets.
- [ ] Validate press-to-transmit latency, first-word capture, final audio delivery, and release-to-finish behavior before building the complete remote UI.

### 2. Add the remote page and shared client code
- [ ] Add an authenticated `/remote?theater_id=...` route in `api_server/pages.py`, a `templates/remote.html` template, and dedicated JS/CSS assets.
- [ ] Add an explicit "Open remote" entry from the theater/canvas. Preserve the theater selection in links and installed-app launch behavior; do not require iPhone detection or installation to use the page.
- [ ] Extract reusable API/state/audio helpers from the canvas where needed, avoiding a copy of the full `canvas.html` script or loading its overlays and renderers.
- [ ] Show theater identity, connection state, agent state, active-orator status, current image/music, and command pending/error feedback.
- [ ] Use large touch targets, safe-area spacing, portrait/landscape layouts, accessible labels, and keyboard-accessible controls.

### 3. Expose core orator controls
- [ ] Provide touch buttons for existing actions: new image/music, previous image/music, next image, image/music pinning, update story, and update UI.
- [ ] Add typed instructions using `/api/orator/command`, plus explicit agent start/stop controls using existing lifecycle endpoints.
- [ ] Reuse `/api/theaters/{theater_id}/orator-action` and existing state APIs; reflect server-confirmed state across the phone and canvas.
- [ ] Enforce active-orator authorization on every control endpoint and audio connection. Revoke control promptly after baton transfer, logout, or session expiry; a theater join key alone must not grant control.
- [ ] Handle duplicate taps, stale state, insufficient credits, and disconnected sessions with clear feedback. Do not replay queued control commands automatically after reconnection.

### 4. Browse and push scenes/music
- [ ] Add compact scene thumbnails and playlist/track browsing using the existing scene and playlist listing endpoints in `api_server/theaters.py`.
- [ ] Push selected scenes/tracks through the existing `/scenes/push` and `/playlists/push` endpoints, preserving their authorization and pinning behavior.
- [ ] Show selection/loading/error feedback and refresh current image/music after a push; keep playback on the canvas device.
- [ ] Lazy-load thumbnails and add search/filtering for large libraries. Asset uploading, editing, generation workflows, and animation management are outside the initial remote scope.

### 5. Provide a plain current-image preview
- [ ] Display the image actually selected on the canvas, including history navigation and manual pushes, using its existing asset URL and shared state.
- [ ] Allow the preview to expand to fill the remote viewport, preserve the complete image aspect ratio, and provide a minimal close/back control.
- [ ] Keep the expanded view image-only: no canvas action wheel, text/UI overlays, interactables, or audio playback. Do not embed the complete canvas page or stream the display device's screen.
- [ ] Keep the preview synchronized while expanded; handle missing images, loading failures, and rapid scene changes without showing a stale load over a newer scene.

### 6. Implement foreground hold-to-talk
- [ ] Capture the phone microphone directly; do not use `/api/orator/toggle_mic`, which broadcasts a toggle to other clients.
- [ ] Add an explicit microphone setup/permission step and visible ready, transmitting, finishing, and unavailable states. Never imply transmission before capture and transport are ready.
- [ ] Use press/release boundaries instead of relying on the current recorder's voice-activity silence timeout. Deliver final encoded audio before sending the end-of-turn boundary.
- [ ] Handle pointer capture, release outside the button, pointer cancellation, and accessible keyboard press/release without a stuck transmitting state.
- [ ] Stop transmission and release microphone resources on page hiding, navigation, socket loss, permission loss, or baton transfer. Cancel pending capture initialization if the press ends before permission/setup completes.
- [ ] Reconnect and refresh state on return, but require a fresh intentional press before transmitting again. Handle microphone permission denial and audio interruptions with a recovery action.
- [ ] Require HTTPS for phone microphone access, including LAN deployment. Keep Narratron responses and music on the canvas to avoid duplicate sound and reduce acoustic feedback.

### 7. Add Home Screen/PWA support
- [ ] Add a manifest, icons, standalone display configuration, and brief iPhone installation guidance; verify authenticated launches and theater selection from the Home Screen.
- [ ] Add a narrowly scoped service worker for versioned static app-shell assets. Keep authenticated APIs, control mutations, microphone streams, and live theater state out of offline caches/queues.
- [ ] Show a clear offline/disconnected state and disable transmission/control actions until reconnected. Offline theater operation is outside scope.
- [ ] Offer an optional keep-screen-awake setting when supported, reacquiring the wake lock when visible. Do not rely on background execution or lock-screen microphone access.
- [ ] Handle shell updates without interrupting an active transmission or leaving HTML/JS versions mismatched.

### 8. Validate and release
- [ ] Follow `skills/narratron-testing/SKILL.md` when implementing tests; run Python checks through `uv run`.
- [ ] Add focused coverage for remote access, active-orator restrictions, scene/music pushes, microphone ownership, audio turn ordering, and disconnect/reconnect behavior.
- [ ] Test Safari and the installed Home Screen app on real iPhones at the chosen minimum and current supported iOS versions; smoke-test Android and desktop access.
- [ ] Exercise denied permissions, rapid short presses, interrupted setup, scene changes during preview, baton transfer, Wi-Fi loss, app switching, screen lock/unlock, incoming calls, and AirPods/Bluetooth routing.
- [ ] Acceptance: phone controls update the separate canvas, the plain image preview follows its current image, phone speech reaches the agent only during an intentional foreground press, and reopening the remote restores current state without unintended commands or duplicate playback.

Delivery order: audio/two-device feasibility first; remote page, controls, and plain image preview next; scene/music browsing and robust hold-to-talk next; PWA installation and device validation last. Native iOS distribution, background/lock-screen voice, screen streaming, and the full canvas interaction/editor experience are outside this scope.
