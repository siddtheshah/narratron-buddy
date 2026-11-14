# Canvas TypeScript migration coverage

The core baseline is commit `b5632e9`, before the TypeScript migration:
17 Python files, 104 test definitions before parametrization. Two definitions
exercise Python backend state/handlers and remain in Python. The other 102 are
frontend migration candidates.

This inventory counts original definitions, not current Playwright tests. Several
TypeScript tests group related behaviors; parametrized cases must also survive.
Keep each Python suite until its replacement passes with the same permissions,
persistence, geometry, and race-condition checks.

| Original file in `testing/ui/` | Baseline definitions | Current coverage |
| --- | ---: | --- |
| `a2ui_canvas_drag_test.py` | 7 | `a2ui-renderer_test.ts`; 15 browser cases covering `a2ui-renderer.ts`: all 5 drag edges (`top`, `bottom`, `left`, `right`, `control`), `[2, 98]` clamping & unmoved no-op, action error re-enable, `pointercancel`/`lostpointercapture` restore, read-only surfaces, remote position deferral, surface lifecycle/delete & error recovery, and `/canvas` integration |
| `canvas_activity_test.py` | 5 | `agent-activity_test.ts`; timeline stability across refreshes, partial updates, dual visual-tool completion, 30s stale lease, and 10m hard timeout |
| `canvas_bookmarks_test.py` | 1 | `bookmarks_test.ts`; includes persistence, role visibility, navigation, failed jumps |
| `canvas_chat_name_test.py` | 5 | `chat-name_test.ts`; 11 browser cases covering `chat-controller.ts`, `frontend/canvas/canvas.ts`, and `auth-flow.ts`: template/script wiring, 4 profile color cases, guest name generation & `localStorage` persistence, login chip & stamp manager auth-modal triggers, `/api/auth/me` deduplication/retry, `testing_use_local` auto-login, and delayed auth race resolution |
| `canvas_collaboration_bar_test.py` | 1 | `collab-bar_test.ts`; viewer bar visibility, collaboration status, modal layout, and role toggling at 1000px and 1400px |
| `canvas_corner_controls_test.py` | 1 | `corner-controls_test.ts`; all 3 corners, 7 visibility masks, edge packing, and expanded panel overlap |
| `canvas_geometry_test.py` | 1 | `renderers_test.ts`; 2 fit modes, each with 4 viewport/zoom combinations at DPR=2 |
| `canvas_navigation_test.py` | 1 | `navigation_test.ts`; pan, zoom, pinch, coordinate conversion, reset, local state |
| `canvas_pin_test.py` | 1 | `pin-control_test.ts`; all 4 pin combinations, API toggling, viewer visibility, and error handling |
| `canvas_resize_test.py` | 1 | `layout-resize_test.ts`; layout-resizing cover across reversed slides, settle callbacks, and viewport resizes |
| `canvas_scenes_characters_test.py` | 23 | `asset-manager_test.ts`; 28 browser cases covering `asset-manager.ts`: HTML/CSS card wiring, playlist accordions/push, animation push & role loss fallback, thumbnails, No Music state, playing track persistence, viewer/orator permissions, scene & character cards/push, descriptions, preloading, badge revert, scroll reset, scene generator presets/errors, `@` reference tagging autocomplete, 3 generation kinds & 3 animation presets, asset search, overlapping scene generation races, and WebSocket `theater_characters_updated`/`theater_scenes_updated`/`theater_animations_updated` refreshes |
| `canvas_segment_select_test.py` | 13 | `doodle-renderer_test.ts`; 18 original cases replaced by 20 browser cases, preserving 6 zoom/pan/reverse combinations and adding permission/history checks |
| `canvas_speech_replay_test.py` | 2 | `scene_test.ts`; 4 browser cases covering `scene.ts`, `speech-replay.ts`, and `/canvas` push-to-talk, `Shift+Space` orator command, VAD 1000ms interruption, mute, and replay button silencing |
| `canvas_stamp_test.py` | 21 | `stamps_test.ts`; 29 browser cases covering `stamps.ts`: HTML/CSS wiring, user/theater stamp separation & search, doodle-disable/orator-enable drag, generation permissions/pending/success & failure, panel tabs at 1400px/1000px for owner/non-owner, toggle, one-of-a-kind drag-and-drop, selection & delete, click-and-hold drag, contributor permission rejection, resize handle/wheel, rotation handle/resize/replay/cancel/keyboard wrap, hover cursors, container resize, multi-stamp drag without snap-back, layering order & MRU persistence, stamps-under-doodles z-order, `@` reference tagging autocomplete, `isSameStampKind`, and live `theater_stamps_updated` WebSocket refresh |
| `canvas_text_edit_test.py` | 3 | `text-annotations_test.ts`; selection, inline editing, Escape cancellation, drag previews, pointer cancellation, replay, and permission checks |
| `canvas_undo_test.py` | 17 | 15 frontend definitions replaced by `doodle-actions_test.ts`; 2 backend tests retained in Python |
| `canvas_zoom_effect_test.py` | 1 | `renderers_test.ts`; 4 fit/source-size cases, each with 5 zoom/resize combinations at DPR=2 |

Current disposition: all 102 original frontend definitions across the 17 core files
have TypeScript replacements, 0 frontend definitions remain pending, and 2 backend
definitions remain in Python (`testing/ui/canvas_undo_test.py`). All 82 production
TypeScript modules in `frontend/canvas/` — the 19 core canvas modules
(`a2ui-renderer.ts`, `agent-activity.ts`, `asset-manager.ts`, `bookmarks.ts`,
`collab-bar.ts`, `cursor-state.ts`, `doodle-actions.ts`, `doodle-renderer.ts`,
`doodle-types.ts`, `image-renderer.ts`, `layout-resize.ts`, `media.ts`,
`navigation.ts`, `pin-control.ts`, `scene.ts`, `speech-replay.ts`, `stamps.ts`,
`state-polling.ts`, and `text-annotations.ts`), 5 adjacent Wave-2 modules
(`action-wheel.ts`, `audio-recorder.ts`, `beyond20-listener.ts`,
`device-aware-pcm.ts`, and `story-log-inspector.ts`), 8 Wave-3 modules
(`animation-effects.ts`, `chat-controller.ts`, `chat-emotes.ts`,
`game-panel.ts`, `game-renderer.ts`, `game-runtime.ts`, `image-effects.ts`,
and `theater-report.ts`), 13 Wave-4 modules (`corner-panels.ts`,
`docs-navigation.ts`, `friends.ts`, `mic-config.ts`, `music-player.ts`,
`narration-pane.ts`, `obs.ts`, `popout.ts`, `profile.ts`, `remote-audio.ts`,
`remote-game.ts`, `remote.ts`, and `user-help.ts`), 14 Wave-5 modules
(`adventures.ts`, `auth-flow.ts`, `baton-manager.ts`, `credit-purchase.ts`,
`device-navigation.ts`, `doodle-session.ts`, `gift.ts`, `history-paging.ts`,
`join-splash.ts`, `remote-login.ts`, `shared-topbar.ts`, `stats.ts`,
`theater-creation.ts`, and `theater-editor.ts`), 21 Wave-6 modules (6 canvas
modules extracted/migrated from `static/js/canvas.js`: `agent-connection.ts`,
`canvas.ts`, `clip-share.ts`, `fullscreen-controls.ts`, `hotkey-controls.ts`,
and `theater-modals.ts`; and 15 testlab modules: `testlab-a2ui-canvas-lab.ts`,
`testlab-adventure-runner.ts`, `testlab-animation-benchmark.ts`,
`testlab-audio-vad.ts`, `testlab-decision-lab.ts`, `testlab-effects-lab.ts`,
`testlab-game-lab.ts`, `testlab-image-benchmark.ts`,
`testlab-layered-animation-lab.ts`, `testlab-music-benchmark.ts`,
`testlab-speech-benchmark.ts`, `testlab-story-planner-lab.ts`,
`testlab-text-effects-lab.ts`, `testlab-text-response-benchmark.ts`, and
`testlab-video-benchmark.ts`), and 2 extracted embedded-JS modules
(`game-panel-bridge.ts` and `pcm-worklet-processor.ts`) — compile to canonical
`/static/generated/canvas/` modules (compatibility shims in `static/js/` and
`testlab/static/` have been retired). Corner-controls wiring and page
orchestration live in `frontend/canvas/canvas.ts`, and `chat-name_test.ts`
covers `chat-controller.ts`, `frontend/canvas/canvas.ts`, and `auth-flow.ts`.
The 4 former Python renderer snippet tests (`doodles_test.py`,
`doodle_history_test.py`, `hide_canvas_text_test.py`, and `sticky_notes_test.py`),
the 6 Wave-3 frontend suites (`theater_report_test.py` and the 5 pure-frontend
`game_*_test.py` suites), the 5 Wave-4 pure-frontend suites (`friends_test.py`,
`obs_rendering_test.py`, `profile_stamps_test.py`, `user_help_chat_test.py`, and
`user_help_markdown_test.py`), and the 6 Wave-5 pure-frontend suites
(`auth_password_reset_test.py`, `auth_signup_test.py`, `auth_state_test.py`,
`join_splash_test.py`, `theater_creation_test.py`, and `theater_editor_test.py`)
have moved to TypeScript in `frontend/canvas/`; `testing/ui/remote_test.py` is
trimmed to only the backend FastAPI/session baton-credits check (all 44
pure-frontend UI tests migrated to `remote_test.ts`), and the Wave-5 split
suites (`adventure_guide_ui_test.py`, `adventure_runner_game_test.py`,
`crossfade_test.py`, `fullscreen_popout_test.py`, `image_history_paging_test.py`,
`obs_canvas_test.py`, and `policy_pages_test.py`) retain only backend/route/testlab
checks in Python while their frontend assertions live in TypeScript.

Additional coverage outside this baseline (83 additional `frontend/canvas/*_test.ts`
suites beyond the 16 baseline-derived suites in the table above — 99 `frontend/canvas/*_test.ts`
suites total — plus `testing/ui/canvas_fixtures_test.ts`,
`testing/ui/game_fixtures.ts`, and `testing/testdata/game_snapshots.json`):

- `accepted-suggestions_test.ts`: replaces `accepted_suggestions_test.py`.
- `action-wheel_test.ts`: replaces `action_wheel_test.py` and covers `action-wheel.ts`.
- `adventure-guide_test.ts`: covers frontend assertions split from `adventure_guide_ui_test.py`.
- `adventure-runner-game_test.ts`: covers frontend game-harness assertions split from `adventure_runner_game_test.py`.
- `adventures_test.ts`: covers `adventures.ts` extracted from `templates/adventures.html`.
- `agent-connection_test.ts`: covers `agent-connection.ts` extracted from `static/js/canvas.js` (insufficient-credits modal, start/stop/status lifecycle, WebSocket connection/audio buffering, mic toggle, and push-to-talk).
- `animation-effects_test.ts`: covers `animation-effects.ts` (including alias, pure helpers, and distortion/mirage rendering).
- `audio-recorder_test.ts`: replaces `audio_recorder_test.cjs` and covers `audio-recorder.ts`.
- `auth-flow_test.ts`: replaces `auth_password_reset_test.py` and `auth_signup_test.py`, and covers `auth-flow.ts` and `remote-login.ts`.
- `auth-state_test.ts`: replaces `auth_state_test.py`.
- `baton-manager_test.ts`: covers `baton-manager.ts` extracted from `static/js/canvas.js`.
- `beyond20-listener_test.ts`: replaces `beyond20_listener_test.py` and covers `beyond20-listener.ts`.
- `canvas_test.ts`: covers `canvas.ts` (auth hydration, orator/viewer UI & `Shift+Space`, `canvas-state` WebSocket dispatch, helpers/globals, and triframe/video animation playback).
- `chat-composer_test.ts`: replaces `chat_composer_test.py`.
- `chat-controller_test.ts`: covers `chat-controller.ts` and `chat-emotes.ts`.
- `chat-highlight_test.ts`: replaces `chat_highlight_test.py`.
- `clip-share_test.ts`: covers `clip-share.ts` extracted from `static/js/canvas.js` (share/OBS link copying and clip-share modal).
- `corner-panels_test.ts`: covers `corner-panels.ts` extracted from `static/js/canvas.js`.
- `credit-purchase_test.ts`: covers `credit-purchase.ts`.
- `crossfade_test.ts`: covers frontend crossfade, `tool_activity`, narration, and mute assertions split from `crossfade_test.py` and `obs_canvas_test.py`.
- `cursor-state_test.ts`: store validation, subscription, interoperability, and real canvas controls.
- `device-navigation_test.ts`: covers `device-navigation.ts`.
- `docs-navigation_test.ts`: covers `docs-navigation.ts`.
- `doodle-history_test.ts`: replaces `doodle_history_test.py`.
- `doodle-session_test.ts`: covers `doodle-session.ts` extracted from `static/js/canvas.js`.
- `doodles_test.ts`: replaces `doodles_test.py`.
- `friends_test.ts`: replaces `friends_test.py` and covers `friends.ts`.
- `fullscreen-controls_test.ts`: covers `fullscreen-controls.ts` extracted from `static/js/canvas.js` (fullscreen toggles, vendor prefixes, cinematic timer, and responsive sidebar collapse).
- `fullscreen_popout_test.ts`: covers frontend responsive sidebar/fullscreen assertions split from `fullscreen_popout_test.py`.
- `game-canvas-stamp_test.ts`: replaces `game_canvas_stamp_test.py`.
- `game-form_test.ts`: replaces `game_form_test.py`.
- `game-panel_test.ts`: replaces `game_panel_test.py` and covers `game-panel.ts` and `game-panel-bridge.ts`.
- `game-renderer_test.ts`: replaces `game_renderer_test.py` and covers `game-renderer.ts`.
- `game-runtime_test.ts`: replaces `game_runtime_test.py` and covers `game-runtime.ts`.
- `gift_test.ts`: covers `gift.ts` extracted from `templates/gift.html`.
- `hide-canvas-text_test.ts`: replaces `hide_canvas_text_test.py`.
- `history-paging_test.ts`: covers `history-paging.ts` extracted from `static/js/canvas.js` and frontend paging checks split from `image_history_paging_test.py`.
- `hotkey-config_test.ts`: replaces `hotkey_config_test.py`.
- `hotkey-controls_test.ts`: covers `hotkey-controls.ts` extracted from `static/js/canvas.js` (hotkey formatting/matching, localStorage persistence, rebinding, text overlay, and mic shortcut dispatch).
- `image-effects_test.ts`: covers `image-effects.ts`.
- `image-renderer_test.ts`: CDN policy, sequence pixels and lifecycle,
  layered composition/effects, silent video fallback and looping, and load cancellation.
- `join-splash_test.ts`: replaces `join_splash_test.py` and covers `join-splash.ts` extracted from `templates/join_splash.html`.
- `media_test.ts`: manifest signatures, URL resolution, timing defaults, option forwarding,
  and input immutability.
- `mic-config_test.ts`: covers `mic-config.ts` extracted from `static/js/canvas.js`.
- `microphone-test_test.ts`: replaces `microphone_test_test.py` and covers `device-aware-pcm.ts` and `pcm-worklet-processor.ts` (including live `OfflineAudioContext` + `AudioWorkletNode` resampling).
- `minimal-mode_test.ts`: replaces `minimal_mode_ui_test.py`.
- `music-mute-persistence_test.ts`: replaces `music_mute_persistence_test.py`.
- `music-player_test.ts`: covers `music-player.ts` extracted from `static/js/canvas.js`.
- `narration-pane_test.ts`: covers `narration-pane.ts`.
- `obs-rendering_test.ts`: replaces `obs_rendering_test.py`.
- `obs_test.ts`: covers `obs.ts` extracted from `templates/obs.html` (including triframe/video animation playback).
- `policy-pages_test.ts`: covers frontend assertions split from `policy_pages_test.py`.
- `popout_test.ts`: covers `popout.ts` extracted from `templates/popout.html`.
- `profile_test.ts`: replaces `profile_stamps_test.py` and covers `profile.ts` extracted from `templates/profile.html`.
- `remote-audio_test.ts`: covers `remote-audio.ts`.
- `remote-game_test.ts`: covers `remote-game.ts`.
- `remote_test.ts`: covers `remote.ts` (including 44 pure-frontend UI suites split from `testing/ui/remote_test.py`), `remote-login.ts`, and `pcm-worklet-processor.ts` (`OfflineAudioContext` worklet resampling).
- `shared-topbar_test.ts`: covers `shared-topbar.ts` extracted from `templates/shared_topbar.html`.
- `state-polling_test.ts`: all 12 original CJS cases against canonical `state-polling.ts`.
- `stats_test.ts`: covers `stats.ts` extracted from `templates/stats.html`.
- `sticky-notes_test.ts`: replaces `sticky_notes_test.py`.
- `story-log-inspector_test.ts`: replaces `story_log_inspector_test.cjs` and covers `story-log-inspector.ts`.
- `suggestions_test.ts`: replaces `suggestions_test.py`.
- `testlab-a2ui-canvas-lab_test.ts`: covers `testlab-a2ui-canvas-lab.ts` extracted from `testlab/a2ui_canvas_lab.html`.
- `testlab-adventure-runner_test.ts`: covers `testlab-adventure-runner.ts` extracted from `testlab/adventure_runner.html`.
- `testlab-animation-benchmark_test.ts`: covers `testlab-animation-benchmark.ts` extracted from `testlab/animation_benchmark.html`.
- `testlab-audio-vad_test.ts`: covers `testlab-audio-vad.ts` and `pcm-worklet-processor.ts` (`OfflineAudioContext` 4096-sample VAD worklet frame emission).
- `testlab-decision-lab_test.ts`: covers `testlab-decision-lab.ts` extracted from `testlab/decision_lab.html`.
- `testlab-effects-lab_test.ts`: covers `testlab-effects-lab.ts` extracted from `testlab/effects_lab.html`.
- `testlab-game-lab_test.ts`: covers `testlab-game-lab.ts` extracted from `testlab/game_lab.html`.
- `testlab-image-benchmark_test.ts`: covers `testlab-image-benchmark.ts` extracted from `testlab/image_benchmark.html`.
- `testlab-layered-animation-lab_test.ts`: covers `testlab-layered-animation-lab.ts` extracted from `testlab/layered_animation_lab.html`.
- `testlab-music-benchmark_test.ts`: covers `testlab-music-benchmark.ts` extracted from `testlab/music_benchmark.html`.
- `testlab-speech-benchmark_test.ts`: covers `testlab-speech-benchmark.ts` extracted from `testlab/speech_benchmark.html`.
- `testlab-story-planner-lab_test.ts`: covers `testlab-story-planner-lab.ts` extracted from `testlab/story_planner_lab.html`.
- `testlab-text-effects-lab_test.ts`: covers `testlab-text-effects-lab.ts` extracted from `testlab/text_effects_lab.html`.
- `testlab-text-response-benchmark_test.ts`: covers `testlab-text-response-benchmark.ts` extracted from `testlab/text_response_benchmark.html`.
- `testlab-video-benchmark_test.ts`: covers `testlab-video-benchmark.ts` extracted from `testlab/video_benchmark.html`.
- `theater-creation_test.ts`: replaces `theater_creation_test.py` and covers `theater-creation.ts` extracted from `templates/theater_creation.html`.
- `theater-editor_test.ts`: replaces `theater_editor_test.py` and covers `theater-editor.ts`.
- `theater-modals_test.ts`: covers `theater-modals.ts` extracted from `static/js/canvas.js` (pullout drawer, Adventure Guide, Viewer Collab Guide, and Theater Config modal).
- `theater-report_test.ts`: replaces `theater_report_test.py` and covers `theater-report.ts`.
- `user-help_test.ts`: replaces `user_help_chat_test.py` and `user_help_markdown_test.py`, and covers `user-help.ts`.
- `testing/ui/canvas_fixtures_test.ts`: asset serving, request isolation, service-worker
  blocking, secondary contexts, and full canvas mounting.
- `testing/ui/triframe_animation_test.py` and `video_animation_ui_test.py` retain
  backend manifest/path/history checks.

Verification: `npm test` runs strict type checking (`npm run typecheck`), the
production build (`npm run build`), and all TypeScript Playwright tests
(`npm run test:ui`; 100 test files: 99 in `frontend/canvas/` plus
`testing/ui/canvas_fixtures_test.ts`). Both legacy CommonJS suites are migrated
and deleted, and `test:cjs` has been removed from `package.json`. Run Python
checks with `uv run pytest`; generated modules are built by the UI fixture when
stale.

## Repository-Wide Migration Status (Beyond the Canvas Core)

The 17-file / 102-definition Canvas core baseline and standalone authored browser
JavaScript/HTML script extractions are complete. All browser code, audio-worklet
processors, iframe bridge logic, and animation UI assertions now execute and are
verified as compiled TypeScript; the 35 legacy `static/js/` compatibility shims,
`testlab/static/audio-vad-lab.js`, and `allowJs: true` have been retired. The
Python backend and vendored `static/js/splitting.min.js` do not require
conversion for this frontend migration.

### Outstanding Work

- [x] Extract embedded JavaScript into compiler-checked TypeScript sources while
  preserving audio-worklet and sandbox behavior:
  - Extracted `runGamePanelBridge` into `frontend/canvas/game-panel-bridge.ts`
    (compiled and type-checked by `tsc`, serialized with `.toString()` inside
    `game-panel.ts` to preserve the CSP nonce inline `<script>` inside
    `sandbox="allow-scripts"`).
  - Extracted `registerPcmWorkletProcessor` and `buildPcmWorkletSource` into
    `frontend/canvas/pcm-worklet-processor.ts`, used by `device-aware-pcm.ts`,
    `remote.ts`, and `testlab-audio-vad.ts` to construct their Blob
    AudioWorklet modules without un-typed string sources; verified with real
    Chromium `OfflineAudioContext` + `AudioWorkletNode` resampling tests.
- [x] Replace frontend source-text assertions in
  `testing/ui/triframe_animation_test.py` and
  `testing/ui/video_animation_ui_test.py` with behavioral TypeScript browser
  assertions in `canvas_test.ts` and `obs_test.ts`, while retaining backend
  manifest, path, state, and history checks in Python.
- [x] Update remaining template/module/test callers to canonical
  `/static/generated/canvas/...` URLs, retire the 35 `static/js/` compatibility
  shims and `testlab/static/audio-vad-lab.js`, and remove `allowJs: true`,
  `checkJs: false`, `"/static/js/*"`, and `.js` include/exclude entries from
  `tsconfig.json` and `tsconfig.build.json`.
  Local startup build-input discovery in `utils/frontend_build.py` also follows
  the TypeScript-only production scope. Auth templates keep the unversioned
  canonical module URL so relative imports share a single auth-state cache.
- [x] Correct stale examples in `skills/narratron-testing/SKILL.md` (removed
  deleted CJS suites, `npm run test:cjs`, `canvas_corner_controls_test.py`, and
  deprecated shim authoring instructions) and update
  `site_docs/game_html_panels.md`.
- [ ] Optional hardening: consolidate shared page/API/WebSocket fixtures and
  tighten payload casts and discriminated unions described below. Consider
  `noUncheckedIndexedAccess` and `exactOptionalPropertyTypes` separately.

Verification on 2026-11-14 covers 82 production TypeScript modules and 100
Playwright test suites (99 in `frontend/canvas/` plus
`testing/ui/canvas_fixtures_test.ts`): `npm test` passed strict typecheck,
production build, and all 615 Chromium tests. The full core Python suite,
`uv run pytest -n 4`, passed all 3249 tests (76 warnings). Ruff passed for the
changed Python files. A Docker build was not run.

Follow-up verification on 2026-11-14 reproduced timing races in the chat
submission/initial-scroll test and the music benchmark note-save test. The
tests now wait for the initial scroll frame and completed chat notifications,
and save notes through a single blur-triggered change event. Both affected
suites passed ten repetitions (60 tests); a fresh `npm test` passed all 615
tests, type checking, and the production build. The full core Python run again
passed 3249 tests (76 warnings), and the three Python suites touched by import
cleanup passed 105 tests. `uv run ruff check .` passed repository-wide.
README, Test Lab setup, the testing skill, and the task list now describe the
completed migration, browser installation, and separate build requirements for
focused UI runs. Docker and live provider evaluations were not run.

### Remaining Raw JavaScript

- **`static/js/` (0 authored raw JS files; 1 vendored classic `<script>` file, 30 lines)**:
  - **0 remaining compatibility shims or authored ES modules**: all 35
    compatibility shims previously in `static/js/` have been removed, and all
    templates, modules, and tests import directly from
    `/static/generated/canvas/`.
  - **1 vendored classic `<script>` file**: `splitting.min.js` (30 lines).
- **Other JS files**: `testlab/static/audio-vad-lab.js` and the empty
  `testlab/static/` directory have been removed (along with `testlab/server.py`'s
  `/static` mount); `scripts/build_canvas.mjs` (73 lines) is the Node
  build/typecheck CLI script.
- **Embedded executable JavaScript**: **0** un-typed embedded JavaScript
  strings remain (worklet processors live in `pcm-worklet-processor.ts` and the
  iframe bridge lives in `game-panel-bridge.ts`).
- **Inline `<script>` blocks in HTML**:
  - **0 remaining non-trivial inline `<script>` templates** in `templates/`
    (`adventures.html`, `gift.html`, `join_splash.html`, `shared_topbar.html`,
    `stats.html`, and `theater_creation.html` are migrated to `type="module"`
    bundles); `obs.html` and `popout.html` retain only 4-line module entrypoints
    (`initObsCanvas()` and `initPopoutChat()`), and `profile.html` has 0 inline
    script blocks.
  - **0 inline `<script>` blocks remain in `testlab/*.html`**: all 15 interactive
    lab/benchmark HTML pages (`a2ui_canvas_lab.html`, `adventure_runner.html`,
    `animation_benchmark.html`, `audio_vad_lab.html`, `decision_lab.html`,
    `effects_lab.html`, `game_lab.html`, `image_benchmark.html`,
    `layered_animation_lab.html`, `music_benchmark.html`, `speech_benchmark.html`,
    `story_planner_lab.html`, `text_effects_lab.html`,
    `text_response_benchmark.html`, and `video_benchmark.html`) load compiled
    `type="module"` bundles (`testlab-*.js`), and `testlab/index.html` contains no
    `<script>` tags (`text_effects_lab.html` also loads `/effects-static/js/splitting.min.js`).
- **Compiler/build scope**: `tsconfig.json` and `tsconfig.build.json` compile and
  type-check only `.ts` sources (`allowJs`, `checkJs`, `"/static/js/*"`, and `.js`
  include/exclude entries have been removed), emitting
  `frontend/canvas/*.ts` -> `static/generated/canvas/*.js` (wiping
  `static/generated/canvas/` on non-watch builds).

### Known Caveats & Watchouts

- **Resolved Wave-1 source-assertion failures**: The two pre-existing Wave-1 source-assertion
  failures in `testing/ui/` (`crossfade_test.py::TestCrossfade::test_canvas_template_contains_agent_activity_indicators`
  and `fullscreen_popout_test.py::TestFullscreenPopout::test_canvas_contains_smaller_monitor_chat_rules`)
  are now resolved in TypeScript (`frontend/canvas/crossfade_test.ts` and
  `frontend/canvas/fullscreen_popout_test.ts`) via real Playwright behavioral tests,
  while both Python files retain only FastAPI/backend checks.
- **Isolated Python test mounts & source-map comments**: Test harnesses with custom
  file allowlists (`testing/ui/remote_test.py`, `testlab/game_lab.py`) must explicitly
  serve new generated dependencies, including `game-panel-bridge.js` and
  `pcm-worklet-processor.js` where used. Snippet loaders must join compiled scripts
  with `"\n"` so trailing `//# sourceMappingURL=` comments do not swallow subsequent
  code. `testlab/server.py` mounts `/static/generated` alongside `/effects-static`,
  verified by `testlab/server_test.py`.
- **Windows worktree cleanup & `node_modules`**: Deleting `share` git worktrees on
  Windows can leave the main working copy's `node_modules/` empty; if `tsc.cmd` or
  Playwright is missing before running `npm test` or `pytest` UI fixtures, run
  `npm ci` to restore it.

### Frontend Tests Still in Python (`testing/ui/`)

Of 16 `*_test.py` files (and 0 `*_test.cjs` files) in `testing/ui/` (all pure
frontend suites have been migrated to TypeScript and deleted, while
`remote_test.py`, the 7 Wave-5 split suites, and the 2 animation suites retain
only backend/route/testlab checks):

- **0 pure-frontend suites** and **0 frontend source-text assertions** remain
  in `testing/ui/` outside `testlab/` / firewall checks (`triframe_animation_test.py`
  and `video_animation_ui_test.py` now test backend `CanvasStateManager` behavior
  only, while triframe and video playback live in `canvas_test.ts` and `obs_test.ts`).
- **1 Python Playwright firewall test**: `network_test.py` (exercises Python's
  `conftest.py` offline firewall).
- **2 testlab Playwright integration suites**: `adventure_runner_game_test.py`
  (frontend game-harness checks split to `adventure-runner-game_test.ts`; Python
  retains testlab server integration) and `game_lab_test.py`.
- **8 FastAPI / `CanvasStateManager` route & manifest suites** retaining only
  backend checks: `adventure_guide_ui_test.py`, `crossfade_test.py`,
  `fullscreen_popout_test.py`, `image_history_paging_test.py`,
  `obs_canvas_test.py`, `policy_pages_test.py`, `triframe_animation_test.py`,
  and `video_animation_ui_test.py`.
- **0 Node `vm` CommonJS suites**: `audio_recorder_test.cjs` and
  `story_log_inspector_test.cjs` were migrated to `frontend/canvas/audio-recorder_test.ts`
  and `frontend/canvas/story-log-inspector_test.ts` and deleted.
- **5 pure Python/backend suites to retain in Python**: `agent_credits_test.py`,
  `canvas_undo_test.py`, `chat_prefix_test.py`, `load_canvas_drawing_test.py`,
  and `remote_test.py`.

### Testlab Python Suites (`testlab/*_test.py`)

All 16 `*_test.py` files in `testlab/` remain for FastAPI route/API/backend
verification (`a2ui_canvas_lab_test.py`, `adventure_runner_test.py`,
`animation_benchmark_test.py`, `beautifier_span_benchmark_test.py`,
`decision_lab_test.py`, `game_harness_test.py`, `game_lab_test.py`,
`image_benchmark_test.py`, `layered_animation_lab_test.py`,
`music_benchmark_test.py`, `server_test.py`, `speech_benchmark_test.py`,
`story_planner_lab_test.py`, `text_effects_lab_test.py`,
`text_response_benchmark_test.py`, and `video_benchmark_test.py`). `server_test.py`
runs Playwright to verify `/static/generated/` asset mounting and `/audio-vad`
module execution; the remaining 15 are FastAPI `TestClient` / unit suites
(`a2ui_canvas_lab_test.py`, `adventure_runner_test.py`, and
`speech_benchmark_test.py` were updated in Wave 6 to verify the served
`/static/generated/canvas/testlab-*.js` modules).

### TypeScript Testing Fixtures Status

- **Existing fixtures (`testing/ui/canvas_fixtures.ts`, `testing/ui/game_fixtures.ts`)**:
  `canvas_fixtures.ts` is used by all 99 `frontend/canvas/*_test.ts` suites (plus
  `testing/ui/canvas_fixtures_test.ts`); it enforces offline HTTP/WebSocket isolation,
  blocks service workers, serves `/static/` assets, and provides `mount()` and
  `openCanvas()`. `testing/ui/game_fixtures.ts` and
  `testing/testdata/game_snapshots.json` provide shared declarative game UI fixtures
  and snapshot payloads for the 5 migrated game suites (`game-canvas-stamp_test.ts`,
  `game-form_test.ts`, `game-panel_test.ts`, `game-renderer_test.ts`,
  `game-runtime_test.ts`), and `readCanvasJsSource` / `sliceCanvasJs` have been
  removed from `game_fixtures.ts`.
- **Gaps**:
  - Fixtures only target `/canvas` and game panels; there are no shared TypeScript
    page/API/WebSocket fixtures yet for `obs`, `popout`, `remote`, `theater-editor`,
    `theater-creation`, `profile`, `join-splash`, `friends`, or `testlab`.
  - 48 TypeScript suites (`action-wheel_test.ts`, `adventure-guide_test.ts`,
    `adventure-runner-game_test.ts`, `adventures_test.ts`, `agent-activity_test.ts`,
    `asset-manager_test.ts`, `auth-state_test.ts`, `beyond20-listener_test.ts`,
    `chat-composer_test.ts`, `chat-highlight_test.ts`,
    `chat-name_test.ts` [reads `templates/canvas.html` and `frontend/canvas/canvas.ts`],
    `collab-bar_test.ts`, `corner-controls_test.ts`, `docs-navigation_test.ts`,
    `doodles_test.ts`, `friends_test.ts`, `game-canvas-stamp_test.ts` [reads crown PNG],
    `gift_test.ts`, `join-splash_test.ts`, `navigation_test.ts`, `obs_test.ts`,
    `obs-rendering_test.ts`, `policy-pages_test.ts`, `popout_test.ts`,
    `profile_test.ts`, `remote_test.ts`, `shared-topbar_test.ts`, `stats_test.ts`,
    all 15 `testlab-*_test.ts` suites, `theater-creation_test.ts`,
    `theater-editor_test.ts`, `theater-modals_test.ts`, `theater-report_test.ts`,
    and `user-help_test.ts`) still read raw HTML/TS/assets from disk via
    `fs.readFileSync` / `fs.promises.readFile` instead of using shared DOM/template
    fixture helpers.
  - Python UI fixture helpers (`testing/ui/conftest.py`, `testing/ui/canvas_source.py`
    [now referencing `/static/generated/canvas/canvas.js`], `testing/ui/base.py`)
    remain required for the remaining Python testlab/route suites.

## TypeScript Type Safety & `any` Audit

- **Strictness & prohibited patterns**:
  - **0** uses of the `any` type across all `.ts` and `.d.ts` files (`frontend/canvas/`,
    `testing/ui/`, `playwright.config.ts`).
  - **0** `@ts-ignore`, `@ts-expect-error`, `@ts-nocheck`, or `eslint-disable`
    suppressions, and **0** `: Function`, `: object`, or `: {}` annotations in
    production TypeScript or fixtures.
  - `tsconfig.json` and `tsconfig.build.json` enforce `strict: true`,
    `noImplicitAny: true`, `noImplicitReturns: true`, `isolatedModules: true`, and
    `verbatimModuleSyntax: true` (`types: []` for the browser build).
  - Temporary declaration files (`animation-effects.d.ts`, `image-effects.d.ts`) have
    been removed now that `animation-effects.ts` and `image-effects.ts` are TypeScript
    modules imported directly by `frontend/canvas/image-renderer.ts` (0 `.d.ts` files
    remain in `frontend/canvas/`).
  - `noUncheckedIndexedAccess` and `exactOptionalPropertyTypes` are not enabled;
    `allowJs` and `checkJs` have been removed from `tsconfig.json` (`.ts` only).
- **Loose typing & casts to tighten in future cleanup**:
  - `DoodleMessage` in `frontend/canvas/doodle-actions.ts` is `Record<string, unknown>`,
    requiring `action as unknown as DoodleMessage` in `frontend/canvas/stamps.ts`.
    Replace with a discriminated union of wire message types.
  - `DoodleAnnotation` in `frontend/canvas/doodle-types.ts` uses one flat interface with
    optional fields across `'draw' | 'text' | 'stamp'`; a discriminated union per kind
    would tighten property access.
  - `A2UIDataModel` and `context` in `frontend/canvas/a2ui-renderer.ts` use
    `Record<string, unknown>` (including a `structuredClone(model) as Record<string, unknown>`
    cast).
  - `frontend/canvas/asset-manager.ts` asserts `await response.json()` and WebSocket
    payloads (`as GenerateApiResponse`, `as PushApiResponse`, `as TheaterPlaylist[]`)
    without runtime guards and uses DOM/event casts (`as HTMLFormElement | null`,
    `as KeyboardEvent`).
  - Wave-3 unguarded JSON/AST/snapshot casts: `frontend/canvas/chat-controller.ts`
    (`await response.json() as ...` across `ChatApplySettingsResult`,
    `SuggestionVoteSummary[]`, `ChatMessage[]`, `PrivateHelpResult`, `ChatPostResult`),
    `frontend/canvas/theater-report.ts` (`(body as ReportErrorResponse).detail`), and
    `frontend/canvas/game-runtime.ts` (`arg as CelAstNode`,
    `Object.create(null) as GameRecord`, `JSON.parse(snapshot) as LocalGameSnapshot`,
    `transition.bindings as readonly GameBinding[]`).
  - Several `frontend/canvas/*_test.ts` files (including `theater-report_test.ts`)
    use `window as unknown as <HarnessWindow>` in `page.evaluate(...)`, `{ ... } as
    unknown as Response` fetch stubs in `stamps_test.ts` (prefer `new Response(...)`),
    and `undefined as unknown as CanvasAssetManager` in `asset-manager_test.ts`
    (prefer `CanvasAssetManager | null`).
