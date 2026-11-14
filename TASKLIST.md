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
- [ ] Reduce direct image file os path checking. Make better interfaces to access image content within a theater.

### Incremental Canvas TypeScript Migration

Move canvas logic and its tests to TypeScript progressively, keeping existing JavaScript callers and the Python server working throughout the migration.

- [x] Add the TypeScript build, watch, and type-check commands, plus TypeScript Playwright test configuration and dependency lockfile.
  - Keep production TypeScript in `frontend/canvas/`, emit browser ES modules to `static/generated/canvas/`, and place TypeScript browser tests in `frontend/canvas/*_test.ts` alongside shared fixtures in `testing/ui/` (while remaining Python tests stay in `testing/ui/`).
  - Include generated assets in local startup and deployment builds without overwriting existing JavaScript source files.
  - Support mixed JavaScript/TypeScript with `allowJs` and initially leave `checkJs` disabled; use strict checking for TypeScript and explicit types at migrated module boundaries.
  - Run a separate `tsc --noEmit` check because Playwright transpiles TypeScript tests without checking their types.
- [x] Add shared browser fixtures that serve real production modules and assets.
  - Preserve HTTP/WebSocket isolation, block service workers, and fail on unexpected requests.
  - Replace source slicing and source-text assertions with module imports and behavior checks where practical.
- [x] Pilot the migration with cursor state and its TypeScript tests.
  - Update existing JavaScript callers to import the compiled module, using one consistent URL for shared singleton state.
  - Verify interoperability before expanding the migration; TypeScript tests can also cover modules that are still JavaScript.
- [x] Migrate bookmarks, doodle actions/undo, navigation, and rendering in small batches, moving their tests alongside the logic.
  - Completed: `bookmarks` (`frontend/canvas/bookmarks.ts`, `frontend/canvas/bookmarks_test.ts`), `doodle actions/undo` (`frontend/canvas/doodle-actions.ts`, `frontend/canvas/doodle-actions_test.ts`, retaining the 2 backend tests in `testing/ui/canvas_undo_test.py`), and `navigation` (`frontend/canvas/navigation.ts`, `frontend/canvas/navigation_test.ts`). Rendering implementation and core rendering tests are now migrated to TypeScript.
  - Image rendering implementation: `frontend/canvas/image-renderer.ts` now owns transitions, sequences, layered animation, video playback, and CDN policy with explicit typed options and controllers. `static/js/canvas-renderers.js` re-exports its public API alongside the typed doodle renderer. Five additional browser tests cover sequence pixels/lifecycle, mesh/layer composition, video silence/loop/fallback/cancellation, and compatibility.
  - Doodle rendering implementation: `frontend/canvas/doodle-renderer.ts` and `doodle-types.ts` define typed annotations, DOM nodes, selection, pointer interactions, stamp transforms, text rendering, and geometry. The renderer compatibility module re-exports both typed factories; remaining non-core Python snippet fixtures read the compiled doodle module.
  - Doodle selection test parity: `frontend/canvas/doodle-renderer_test.ts` replaces all 13 definitions / 18 parametrized cases from `canvas_segment_select_test.py` with 20 browser cases. Uses real renderer/CSS imports, full-canvas drag-over behavior, and adds permission/history checks.
  - Shared animation dispatch: `frontend/canvas/media.ts` defines typed triframe/layered/video manifests and preserves signature, URL resolution, defaults, and renderer option forwarding. Canvas and OBS use the compiled module; the legacy media module re-exports it.
  - Rendering test parity: `frontend/canvas/renderers_test.ts` replaces `canvas_geometry_test.py` and `canvas_zoom_effect_test.py` (2 Python test definitions, 6 parametrized cases), importing real modules and CSS through the shared offline fixture. Preserves contain/cover, DPR=2, rotated stamps, canonical text sizing, segment hit testing, pointer normalization, zoom, pan, resize, and image-effect alignment.
  - Text annotations: `frontend/canvas/text-annotations.ts` and `frontend/canvas/text-annotations_test.ts` extract inline text editor/move orchestration from `static/js/canvas.js` and replace all 3 definitions in `canvas_text_edit_test.py`.
  - Canvas controls & layout: extracted `pin-control.ts` (`pin-control_test.ts`), `layout-resize.ts` (`layout-resize_test.ts`), `collab-bar.ts` (`collab-bar_test.ts`), `agent-activity.ts` (`agent-activity_test.ts`), and `corner-controls_test.ts`, replacing `canvas_pin_test.py`, `canvas_resize_test.py`, `canvas_collaboration_bar_test.py`, `canvas_activity_test.py`, and `canvas_corner_controls_test.py`.
- [x] Continue with asset-manager behavior, canvas controls, and A2UI surfaces; extract smaller typed modules from `static/js/canvas.js` as needed.
  - A2UI rendering: `frontend/canvas/a2ui-renderer.ts` (`a2ui-renderer_test.ts`, 15 browser cases) replaces `a2ui_canvas_drag_test.py` (7 baseline definitions), with `static/js/a2ui-canvas-renderer.js` preserved as a `@deprecated` re-export shim.
  - Scene & speech replay: `frontend/canvas/scene.ts` (with `static/js/canvas-scene.js` shim) and `frontend/canvas/speech-replay.ts` (`scene_test.ts`, 4 browser cases) replace `canvas_speech_replay_test.py` (2 baseline definitions).
  - Asset manager: `frontend/canvas/asset-manager.ts` (`asset-manager_test.ts`, 28 browser cases) extracts scenes, characters, playlists, and animations management from `static/js/canvas.js`, replacing `canvas_scenes_characters_test.py` (23 baseline definitions).
  - Stamps: `frontend/canvas/stamps.ts` (`stamps_test.ts`, 29 browser cases) extracts stamp controller, generation queue, MRU persistence, reference tagging/autocomplete, and asset search from `static/js/canvas.js`, replacing `canvas_stamp_test.py` (21 baseline definitions).
  - Chat identity: `frontend/canvas/chat-name_test.ts` (11 browser cases) tests real `chat-controller.js`, `canvas.js`, `auth-flow.js`, and `templates/canvas.html` wiring, replacing `canvas_chat_name_test.py` (5 baseline definitions).
- [x] Track coverage parity for the initial core: 17 Python files and 104 test definitions before parametrization (`canvas*_test.py` and `a2ui_canvas_drag_test.py`).
  - Coverage inventory: `frontend/canvas/MIGRATION.md` records all 17 baseline files and their replacements. Current disposition: all 102 frontend baseline definitions migrated, 0 pending, 2 backend definitions retained in `testing/ui/canvas_undo_test.py`.
  - Retained the two Python backend tests in `testing/ui/canvas_undo_test.py`; all 16 superseded Python frontend baseline suites have been removed after passing replacement checks.
  - Completed state polling: `frontend/canvas/state-polling.ts` and `state-polling_test.ts` (13 browser tests: all 12 former CJS cases plus module-shim compatibility); canvas, OBS, and popout import the compiled module directly.
- [x] Migrate remaining CJS test suites (`testing/ui/story_log_inspector_test.cjs` and `testing/ui/audio_recorder_test.cjs`) to TypeScript.
  - Migrated `story-log-inspector.ts` (`story-log-inspector_test.ts`), `audio-recorder.ts` (`audio-recorder_test.ts`), and `device-aware-pcm.ts` (`microphone-test_test.ts`, replacing `testing/ui/microphone_test_test.py`) with `@deprecated` re-export shims in `static/js/`.
  - Deleted `testing/ui/story_log_inspector_test.cjs` and `testing/ui/audio_recorder_test.cjs`, and removed the obsolete `test:cjs` script from `package.json`.
- [x] Follow with adjacent canvas suites, including action wheel, doodle history, doodles, and hotkey configuration.
  - Migrated `action-wheel.ts` (`action-wheel_test.ts`) and `beyond20-listener.ts` (`beyond20-listener_test.ts`) with `@deprecated` re-export shims in `static/js/`.
  - Replaced 13 additional Python frontend suites with TypeScript Playwright suites in `frontend/canvas/`: `accepted-suggestions_test.ts`, `chat-composer_test.ts`, `chat-highlight_test.ts`, `doodle-history_test.ts`, `doodles_test.ts`, `hide-canvas-text_test.ts`, `hotkey-config_test.ts`, `minimal-mode_test.ts`, `music-mute-persistence_test.ts`, `sticky-notes_test.ts`, and `suggestions_test.ts` (14 Python suites and 2 CJS suites replaced in total across Wave 2).
- [ ] Migrate remaining raw JavaScript and Python frontend/mixed UI suites to TypeScript.
  - Completed Wave 3 (8 production `.ts` modules and 9 Playwright test suites, bringing `frontend/canvas/` to 32 production modules and 45 `*_test.ts` suites plus `testing/ui/canvas_fixtures_test.ts`): migrated `theater-report.ts` (`theater-report_test.ts`), `chat-emotes.ts` + `chat-controller.ts` (`chat-controller_test.ts`), `image-effects.ts` + `animation-effects.ts` (`image-effects_test.ts`, `animation-effects_test.ts`), and `game-runtime.ts` + `game-panel.ts` + `game-renderer.ts` (`game-runtime_test.ts`, `game-panel_test.ts`, `game-renderer_test.ts`, `game-form_test.ts`, `game-canvas-stamp_test.ts`, plus `testing/ui/game_fixtures.ts` & `testing/testdata/game_snapshots.json`), leaving `@deprecated` re-export shims in `static/js/` and deleting the 6 superseded Python suites (`theater_report_test.py` and the 5 pure-frontend `game_*_test.py` files).
  - Completed Wave 4 (13 production `.ts` modules and 14 Playwright test suites, bringing `frontend/canvas/` to 45 production modules and 59 `*_test.ts` suites plus `testing/ui/canvas_fixtures_test.ts`):
    - Remote: migrated `remote-audio.ts` (`remote-audio_test.ts`), `remote-game.ts` (`remote-game_test.ts`), and `remote.ts` (`remote_test.ts`, migrating 44 pure-frontend UI tests out of `testing/ui/remote_test.py` and retaining only the backend FastAPI/baton-credits check in Python), leaving `@deprecated` shims in `static/js/`.
    - OBS & Popout: migrated `narration-pane.ts` (`narration-pane_test.ts`, with `@deprecated` shim in `static/js/`), `obs.ts` (`obs_test.ts`, extracting `templates/obs.html` inline script), `popout.ts` (`popout_test.ts`, extracting `templates/popout.html` inline script), and `obs-rendering_test.ts` (replacing and deleting `testing/ui/obs_rendering_test.py`); also fixed the `testlab/server.py` `/static/generated` mount watchout with `testlab/server_test.py`.
    - Canvas extractions: extracted `corner-panels.ts` (`corner-panels_test.ts`), `music-player.ts` (`music-player_test.ts`), and `mic-config.ts` (`mic-config_test.ts`) from `static/js/canvas.js` (reducing `canvas.js` from 5,645 to 4,508 lines).
    - Help, Docs, Friends, & Profile: migrated `user-help.ts` (`user-help_test.ts`), `docs-navigation.ts` (`docs-navigation_test.ts`), `friends.ts` (`friends_test.ts`), and `profile.ts` (`profile_test.ts`, extracting `templates/profile.html` inline script), switching template `<script>` tags to `type="module"` and deleting the 4 superseded Python suites (`user_help_chat_test.py`, `user_help_markdown_test.py`, `friends_test.py`, `profile_stamps_test.py`).
  - Remaining raw JS in `static/js/` (7 non-shim files, 6,886 lines): 1 ES module (`canvas.js` at 4,508 lines) plus 6 classic non-module `<script>` files (`theater-editor.js` at 1,502, `auth-flow.js` at 531, `credit-purchase.js` at 285, `device-navigation.js` at 22, `remote-login.js` at 8, and vendored `splitting.min.js` at 30), plus `testlab/static/audio-vad-lab.js` (297) and inline `<script>` blocks in `templates/` (`theater_creation.html`, `adventures.html`, `stats.html`, `join_splash.html`, `shared_topbar.html`, `gift.html`) and `testlab/`.
  - Remaining Python UI suites in `testing/ui/` (22 `*_test.py` files): 6 pure-frontend Playwright suites (`auth_password_reset_test.py`, `auth_signup_test.py`, `join_splash_test.py`, `network_test.py`, `theater_creation_test.py`, `theater_editor_test.py`), 1 pure-frontend source-assertion suite (`auth_state_test.py`), 10 mixed backend + frontend suites, and 5 pure backend/state suites to retain in Python (`agent_credits_test.py`, `canvas_undo_test.py`, `chat_prefix_test.py`, `load_canvas_drawing_test.py`, `remote_test.py`).
  - Watchouts: (1) migrating the remaining 5 authored classic `<script>` files requires switching template `<script>` tags to `type="module"` (auditing load timing/globals) or adding a non-module/shared build output outside `static/generated/canvas/`; (2) `testing/ui/crossfade_test.py` and `testing/ui/fullscreen_popout_test.py` each have 1 pre-existing Wave-1 source-assertion failure against moved canvas code (`agent-activity.ts`, `layout-resize.ts`).

