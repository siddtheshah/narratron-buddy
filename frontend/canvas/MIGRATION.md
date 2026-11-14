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
| `a2ui_canvas_drag_test.py` | 7 | `a2ui-renderer_test.ts`; 15 browser cases covering `a2ui-renderer.ts` and `static/js/a2ui-canvas-renderer.js` shim: all 5 drag edges (`top`, `bottom`, `left`, `right`, `control`), `[2, 98]` clamping & unmoved no-op, action error re-enable, `pointercancel`/`lostpointercapture` restore, read-only surfaces, remote position deferral, surface lifecycle/delete & error recovery, and `/canvas` integration |
| `canvas_activity_test.py` | 5 | `agent-activity_test.ts`; timeline stability across refreshes, partial updates, dual visual-tool completion, 30s stale lease, and 10m hard timeout |
| `canvas_bookmarks_test.py` | 1 | `bookmarks_test.ts`; includes persistence, role visibility, navigation, failed jumps |
| `canvas_chat_name_test.py` | 5 | `chat-name_test.ts`; 11 browser cases covering `/static/js/chat-controller.js`, `/static/js/canvas.js`, and `/static/js/auth-flow.js`: template/script wiring, 4 profile color cases, guest name generation & `localStorage` persistence, login chip & stamp manager auth-modal triggers, `/api/auth/me` deduplication/retry, `testing_use_local` auto-login, and delayed auth race resolution |
| `canvas_collaboration_bar_test.py` | 1 | `collab-bar_test.ts`; viewer bar visibility, collaboration status, modal layout, and role toggling at 1000px and 1400px |
| `canvas_corner_controls_test.py` | 1 | `corner-controls_test.ts`; all 3 corners, 7 visibility masks, edge packing, and expanded panel overlap |
| `canvas_geometry_test.py` | 1 | `renderers_test.ts`; 2 fit modes, each with 4 viewport/zoom combinations at DPR=2 |
| `canvas_navigation_test.py` | 1 | `navigation_test.ts`; pan, zoom, pinch, coordinate conversion, reset, local state |
| `canvas_pin_test.py` | 1 | `pin-control_test.ts`; all 4 pin combinations, API toggling, viewer visibility, and error handling |
| `canvas_resize_test.py` | 1 | `layout-resize_test.ts`; layout-resizing cover across reversed slides, settle callbacks, and viewport resizes |
| `canvas_scenes_characters_test.py` | 23 | `asset-manager_test.ts`; 28 browser cases covering `asset-manager.ts`: HTML/CSS card wiring, playlist accordions/push, animation push & role loss fallback, thumbnails, No Music state, playing track persistence, viewer/orator permissions, scene & character cards/push, descriptions, preloading, badge revert, scroll reset, scene generator presets/errors, `@` reference tagging autocomplete, 3 generation kinds & 3 animation presets, asset search, overlapping scene generation races, and WebSocket `theater_characters_updated`/`theater_scenes_updated`/`theater_animations_updated` refreshes |
| `canvas_segment_select_test.py` | 13 | `doodle-renderer_test.ts`; 18 original cases replaced by 20 browser cases, preserving 6 zoom/pan/reverse combinations and adding permission/history checks |
| `canvas_speech_replay_test.py` | 2 | `scene_test.ts`; 4 browser cases covering `scene.ts`, `static/js/canvas-scene.js` shim, `speech-replay.ts`, and `/canvas` push-to-talk, `Shift+Space` orator command, VAD 1000ms interruption, mute, and replay button silencing |
| `canvas_stamp_test.py` | 21 | `stamps_test.ts`; 29 browser cases covering `stamps.ts`: HTML/CSS wiring, user/theater stamp separation & search, doodle-disable/orator-enable drag, generation permissions/pending/success & failure, panel tabs at 1400px/1000px for owner/non-owner, toggle, one-of-a-kind drag-and-drop, selection & delete, click-and-hold drag, contributor permission rejection, resize handle/wheel, rotation handle/resize/replay/cancel/keyboard wrap, hover cursors, container resize, multi-stamp drag without snap-back, layering order & MRU persistence, stamps-under-doodles z-order, `@` reference tagging autocomplete, `isSameStampKind`, and live `theater_stamps_updated` WebSocket refresh |
| `canvas_text_edit_test.py` | 3 | `text-annotations_test.ts`; selection, inline editing, Escape cancellation, drag previews, pointer cancellation, replay, and permission checks |
| `canvas_undo_test.py` | 17 | 15 frontend definitions replaced by `doodle-actions_test.ts`; 2 backend tests retained in Python |
| `canvas_zoom_effect_test.py` | 1 | `renderers_test.ts`; 4 fit/source-size cases, each with 5 zoom/resize combinations at DPR=2 |

Current disposition: all 102 original frontend definitions across the 17 core files
have TypeScript replacements, 0 frontend definitions remain pending, and 2 backend
definitions remain in Python (`testing/ui/canvas_undo_test.py`). All 59 production
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
`remote-game.ts`, `remote.ts`, and `user-help.ts`), plus 14 Wave-5 modules
(`adventures.ts`, `auth-flow.ts`, `baton-manager.ts`, `credit-purchase.ts`,
`device-navigation.ts`, `doodle-session.ts`, `gift.ts`, `history-paging.ts`,
`join-splash.ts`, `remote-login.ts`, `shared-topbar.ts`, `stats.ts`,
`theater-creation.ts`, and `theater-editor.ts`) — compile to
`static/generated/canvas/`, with re-export shims preserved in `static/js/` where
legacy/OBS/remote/test callers import them. Corner-controls wiring remains in
`static/js/canvas.js` (covered by `corner-controls_test.ts`), and
`chat-name_test.ts` covers `chat-controller.ts` (via `/static/js/chat-controller.js`
shim), `static/js/canvas.js`, and `auth-flow.ts` (via `/static/js/auth-flow.js` shim).
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

Additional coverage outside this baseline (58 additional `frontend/canvas/*_test.ts`
suites beyond the 20 baseline-derived suites, plus `testing/ui/canvas_fixtures_test.ts`,
`testing/ui/game_fixtures.ts`, and `testing/testdata/game_snapshots.json`):

- `accepted-suggestions_test.ts`: replaces `accepted_suggestions_test.py`.
- `action-wheel_test.ts`: replaces `action_wheel_test.py` and covers `action-wheel.ts`.
- `adventure-guide_test.ts`: covers frontend assertions split from `adventure_guide_ui_test.py`.
- `adventure-runner-game_test.ts`: covers frontend game-harness assertions split from `adventure_runner_game_test.py`.
- `adventures_test.ts`: covers `adventures.ts` extracted from `templates/adventures.html`.
- `animation-effects_test.ts`: covers `animation-effects.ts` and `/static/js/animation-effects.js` shim parity.
- `audio-recorder_test.ts`: replaces `audio_recorder_test.cjs` and covers `audio-recorder.ts`.
- `auth-flow_test.ts`: replaces `auth_password_reset_test.py` and `auth_signup_test.py`, and covers `auth-flow.ts` + `/static/js/auth-flow.js` shim parity.
- `auth-state_test.ts`: replaces `auth_state_test.py`.
- `baton-manager_test.ts`: covers `baton-manager.ts` extracted from `static/js/canvas.js`.
- `beyond20-listener_test.ts`: replaces `beyond20_listener_test.py` and covers `beyond20-listener.ts`.
- `chat-composer_test.ts`: replaces `chat_composer_test.py`.
- `chat-controller_test.ts`: covers `chat-controller.ts`, `chat-emotes.ts`, and `/static/js/chat-{controller,emotes}.js` shim parity.
- `chat-highlight_test.ts`: replaces `chat_highlight_test.py`.
- `corner-panels_test.ts`: covers `corner-panels.ts` extracted from `static/js/canvas.js`.
- `credit-purchase_test.ts`: covers `credit-purchase.ts` and `/static/js/credit-purchase.js` shim parity.
- `crossfade_test.ts`: covers frontend crossfade, `tool_activity`, narration, and mute assertions split from `crossfade_test.py` and `obs_canvas_test.py`.
- `cursor-state_test.ts`: store validation, subscription, interoperability, and real canvas controls.
- `device-navigation_test.ts`: covers `device-navigation.ts` and `/static/js/device-navigation.js` shim parity.
- `docs-navigation_test.ts`: covers `docs-navigation.ts` and `/static/js/docs-navigation.js` shim parity.
- `doodle-history_test.ts`: replaces `doodle_history_test.py`.
- `doodle-session_test.ts`: covers `doodle-session.ts` extracted from `static/js/canvas.js`.
- `doodles_test.ts`: replaces `doodles_test.py`.
- `friends_test.ts`: replaces `friends_test.py` and covers `friends.ts` + `/static/js/friends.js` shim parity.
- `fullscreen_popout_test.ts`: covers frontend responsive sidebar/fullscreen assertions split from `fullscreen_popout_test.py`.
- `game-canvas-stamp_test.ts`: replaces `game_canvas_stamp_test.py`.
- `game-form_test.ts`: replaces `game_form_test.py`.
- `game-panel_test.ts`: replaces `game_panel_test.py` and covers `game-panel.ts`.
- `game-renderer_test.ts`: replaces `game_renderer_test.py` and covers `game-renderer.ts`.
- `game-runtime_test.ts`: replaces `game_runtime_test.py` and covers `game-runtime.ts`.
- `gift_test.ts`: covers `gift.ts` extracted from `templates/gift.html`.
- `hide-canvas-text_test.ts`: replaces `hide_canvas_text_test.py`.
- `history-paging_test.ts`: covers `history-paging.ts` extracted from `static/js/canvas.js` and frontend paging checks split from `image_history_paging_test.py`.
- `hotkey-config_test.ts`: replaces `hotkey_config_test.py`.
- `image-effects_test.ts`: covers `image-effects.ts` and `/static/js/image-effects.js` shim parity.
- `image-renderer_test.ts`: CDN policy, shim identity, sequence pixels and lifecycle,
  layered composition/effects, silent video fallback and looping, and load cancellation.
- `join-splash_test.ts`: replaces `join_splash_test.py` and covers `join-splash.ts` extracted from `templates/join_splash.html`.
- `media_test.ts`: manifest signatures, URL resolution, timing defaults, option forwarding,
  input immutability, and legacy shim identity.
- `mic-config_test.ts`: covers `mic-config.ts` extracted from `static/js/canvas.js`.
- `microphone-test_test.ts`: replaces `microphone_test_test.py` and covers `device-aware-pcm.ts`.
- `minimal-mode_test.ts`: replaces `minimal_mode_ui_test.py`.
- `music-mute-persistence_test.ts`: replaces `music_mute_persistence_test.py`.
- `music-player_test.ts`: covers `music-player.ts` extracted from `static/js/canvas.js`.
- `narration-pane_test.ts`: covers `narration-pane.ts` and `/static/js/narration-pane.js` shim parity.
- `obs-rendering_test.ts`: replaces `obs_rendering_test.py`.
- `obs_test.ts`: covers `obs.ts` extracted from `templates/obs.html`.
- `policy-pages_test.ts`: covers frontend assertions split from `policy_pages_test.py`.
- `popout_test.ts`: covers `popout.ts` extracted from `templates/popout.html`.
- `profile_test.ts`: replaces `profile_stamps_test.py` and covers `profile.ts` extracted from `templates/profile.html`.
- `remote-audio_test.ts`: covers `remote-audio.ts` and `/static/js/remote-audio.js` shim parity.
- `remote-game_test.ts`: covers `remote-game.ts` and `/static/js/remote-game.js` shim parity.
- `remote_test.ts`: covers `remote.ts` (including 44 pure-frontend UI suites split from `testing/ui/remote_test.py`), `remote-login.ts`, and `/static/js/remote.js` shim parity.
- `shared-topbar_test.ts`: covers `shared-topbar.ts` extracted from `templates/shared_topbar.html`.
- `state-polling_test.ts`: all 12 original CJS cases plus module-shim compatibility.
- `stats_test.ts`: covers `stats.ts` extracted from `templates/stats.html`.
- `sticky-notes_test.ts`: replaces `sticky_notes_test.py`.
- `story-log-inspector_test.ts`: replaces `story_log_inspector_test.cjs` and covers `story-log-inspector.ts`.
- `suggestions_test.ts`: replaces `suggestions_test.py`.
- `theater-creation_test.ts`: replaces `theater_creation_test.py` and covers `theater-creation.ts` extracted from `templates/theater_creation.html`.
- `theater-editor_test.ts`: replaces `theater_editor_test.py` and covers `theater-editor.ts` + `/static/js/theater-editor.js` shim parity.
- `theater-report_test.ts`: replaces `theater_report_test.py` and covers `theater-report.ts`.
- `user-help_test.ts`: replaces `user_help_chat_test.py` and `user_help_markdown_test.py`, and covers `user-help.ts` + `/static/js/user-help.js` shim parity.
- `testing/ui/canvas_fixtures_test.ts`: asset serving, request isolation, service-worker
  blocking, secondary contexts, and full canvas mounting.
- `testing/ui/triframe_animation_test.py` and `video_animation_ui_test.py` retain
  backend manifest/path/history checks.

Verification: `npm test` runs strict type checking (`npm run typecheck`), the
production build (`npm run build`), and all TypeScript Playwright tests
(`npm run test:ui`; 79 test files: 78 in `frontend/canvas/` plus
`testing/ui/canvas_fixtures_test.ts`). Both legacy CommonJS suites are migrated
and deleted, and `test:cjs` has been removed from `package.json`. Run Python
checks with `uv run pytest`; generated modules are built by the UI fixture when
stale.

## Repository-Wide Migration Status (Beyond the Canvas Core)

While the 17-file / 102-definition Canvas core baseline above is complete, the full
repository migration (no raw JS, all frontend tests in TypeScript with shared TS
fixtures) remains incomplete:

### Remaining Raw JavaScript

- **`static/js/` (2 non-shim files, 2,938 lines)**:
  - **1 remaining ES module**: `canvas.js` (2,908 lines, reduced from 4,508 in Wave 4 and 5,645 in Wave 3).
  - **1 vendored classic `<script>` file**: `splitting.min.js` (30 lines). **0 authored classic non-module `<script>` files remain** (`auth-flow.js`, `credit-purchase.js`, `device-navigation.js`, `remote-login.js`, and `theater-editor.js` are now `@deprecated` re-export shims, and their template callers use `type="module"`).
- **`static/js/` (34 re-export shims: 33 `@deprecated`, plus `canvas-renderers.js`)**:
  `a2ui-canvas-renderer.js`, `action-wheel.js`, `animation-effects.js`,
  `audio-recorder.js`, `auth-flow.js`, `beyond20-listener.js`, `canvas-bookmarks.js`,
  `canvas-cursor-state.js`, `canvas-doodle-actions.js`, `canvas-media.js`,
  `canvas-navigation.js`, `canvas-renderers.js` (lacks `@deprecated` JSDoc),
  `canvas-scene.js`, `canvas-state-polling.js`, `chat-controller.js`,
  `chat-emotes.js`, `credit-purchase.js`, `device-aware-pcm.js`,
  `device-navigation.js`, `docs-navigation.js`, `friends.js`, `game-panel.js`,
  `game-renderer.js`, `game-runtime.js`, `image-effects.js`, `narration-pane.js`,
  `remote-audio.js`, `remote-game.js`, `remote-login.js`, `remote.js`,
  `story-log-inspector.js`, `theater-editor.js`, `theater-report.js`, and
  `user-help.js` remain for legacy/template/test callers.
- **Other raw JS files**: `testlab/static/audio-vad-lab.js` (297 lines) and
  `scripts/build_canvas.mjs` (73 lines). Both CommonJS test files
  (`testing/ui/audio_recorder_test.cjs` and `testing/ui/story_log_inspector_test.cjs`)
  have been migrated to TypeScript and deleted.
- **Inline `<script>` blocks in HTML**:
  - **0 remaining non-trivial inline `<script>` templates** in `templates/`
    (`adventures.html`, `gift.html`, `join_splash.html`, `shared_topbar.html`,
    `stats.html`, and `theater_creation.html` are now migrated to `type="module"`
    bundles); `obs.html` and `popout.html` retain only 4-line module entrypoints
    (`initObsCanvas()` and `initPopoutChat()`), and `profile.html` has 0 inline
    script blocks.
  - 14 lab HTML pages in `testlab/` (including `adventure_runner.html` at 756 lines,
    `decision_lab.html` at 633, `layered_animation_lab.html` at 543,
    `animation_benchmark.html` at 300, `game_lab.html` at 291, `text_effects_lab.html`
    at 236, and `video_benchmark.html` at 229).
- **Compiler/build scope**: `tsconfig.json` keeps `"allowJs": true` and `"checkJs": false`
  (with `"/static/js/*"` mapped in `paths`), and `tsconfig.build.json` /
  `scripts/build_canvas.mjs` only emit `frontend/canvas/` -> `static/generated/canvas/`
  (wiping `static/generated/canvas/` on non-watch builds). Non-canvas targets (`testlab/`)
  and remaining `canvas.js` extraction will need completion before raw JS can be retired.

### Known Caveats & Watchouts

- **Resolved Wave-1 source-assertion failures**: The two pre-existing Wave-1 source-assertion
  failures in `testing/ui/` (`crossfade_test.py::TestCrossfade::test_canvas_template_contains_agent_activity_indicators`
  and `fullscreen_popout_test.py::TestFullscreenPopout::test_canvas_contains_smaller_monitor_chat_rules`)
  are now resolved in TypeScript (`frontend/canvas/crossfade_test.ts` and
  `frontend/canvas/fullscreen_popout_test.ts`) via real Playwright behavioral tests,
  while both Python files retain only FastAPI/backend checks.
- **Isolated Python test mounts & source-map comments**: Test harnesses with custom
  file allowlists (`testing/ui/remote_test.py`, `testlab/game_lab.py`) must explicitly
  serve `static/generated/canvas/*.js` whenever an imported module moves behind a
  `static/js/` shim, and snippet loaders must join compiled scripts with `"\n"` so trailing
  `//# sourceMappingURL=` comments do not swallow subsequent code. (`testlab/server.py`
  now mounts `/static/generated` before `/static`, verified by `testlab/server_test.py`.)
- **Windows worktree cleanup & `node_modules`**: Deleting `share` git worktrees on
  Windows can leave the main working copy's `node_modules/` empty; if `tsc.cmd` or
  Playwright is missing before running `npm test` or `pytest` UI fixtures, run
  `npm ci` to restore it.

### Frontend Tests Still in Python (`testing/ui/`)

Of 16 `*_test.py` files (and 0 `*_test.cjs` files) in `testing/ui/` (the 6 Wave-3
pure-frontend suites, 5 Wave-4 pure-frontend suites, and 6 Wave-5 pure-frontend
suites — `auth_password_reset_test.py`, `auth_signup_test.py`, `auth_state_test.py`,
`join_splash_test.py`, `theater_creation_test.py`, and `theater_editor_test.py` — have
been migrated to TypeScript and deleted, while `remote_test.py` and the 7 Wave-5
split suites have been trimmed to backend/route/testlab checks):

- **0 pure-frontend Playwright or source-assertion tests** remain in `testing/ui/`
  outside `testlab/` / firewall checks.
- **1 Python Playwright firewall test**: `network_test.py` (exercises Python's
  `conftest.py` offline firewall).
- **2 testlab Playwright integration suites**: `adventure_runner_game_test.py`
  (frontend game-harness checks split to `adventure-runner-game_test.ts`; Python
  retains testlab server integration) and `game_lab_test.py`.
- **8 backend FastAPI / `CanvasStateManager` route & manifest suites** (frontend
  assertions moved to TypeScript; Python retains route/backend state checks):
  `adventure_guide_ui_test.py`, `crossfade_test.py`, `fullscreen_popout_test.py`,
  `image_history_paging_test.py`, `obs_canvas_test.py`, `policy_pages_test.py`,
  `triframe_animation_test.py`, and `video_animation_ui_test.py`.
- **0 Node `vm` CommonJS suites**: `audio_recorder_test.cjs` and
  `story_log_inspector_test.cjs` were migrated to `frontend/canvas/audio-recorder_test.ts`
  and `frontend/canvas/story-log-inspector_test.ts` and deleted.
- **5 pure Python/backend suites to retain in Python**: `agent_credits_test.py`,
  `canvas_undo_test.py`, `chat_prefix_test.py`, `load_canvas_drawing_test.py`,
  and `remote_test.py`.

### TypeScript Testing Fixtures Status

- **Existing fixtures (`testing/ui/canvas_fixtures.ts`, `testing/ui/game_fixtures.ts`)**:
  `canvas_fixtures.ts` is used by all 78 `frontend/canvas/*_test.ts` suites (plus
  `testing/ui/canvas_fixtures_test.ts`); it enforces offline HTTP/WebSocket isolation,
  blocks service workers, serves `/static/` assets, and provides `mount()` and
  `openCanvas()`. `testing/ui/game_fixtures.ts` and
  `testing/testdata/game_snapshots.json` provide shared declarative game UI fixtures
  and snapshot payloads for the 5 migrated game suites (`game-canvas-stamp_test.ts`,
  `game-form_test.ts`, `game-panel_test.ts`, `game-renderer_test.ts`,
  `game-runtime_test.ts`).
- **Gaps**:
  - Fixtures only target `/canvas` and game panels; there are no shared TypeScript
    page/API/WebSocket fixtures yet for `obs`, `popout`, `remote`, `theater-editor`,
    `theater-creation`, `profile`, `join-splash`, `friends`, or `testlab`.
  - 32 TypeScript suites (`action-wheel_test.ts`, `adventure-guide_test.ts`,
    `adventure-runner-game_test.ts`, `adventures_test.ts`, `agent-activity_test.ts`,
    `asset-manager_test.ts`, `auth-state_test.ts`, `beyond20-listener_test.ts`,
    `chat-composer_test.ts`, `chat-highlight_test.ts`,
    `chat-name_test.ts` [also reads `static/js/canvas.js`], `collab-bar_test.ts`,
    `corner-controls_test.ts`, `docs-navigation_test.ts`, `doodles_test.ts`,
    `friends_test.ts`, `game-canvas-stamp_test.ts` [reads crown PNG], `gift_test.ts`,
    `join-splash_test.ts`, `navigation_test.ts`, `obs_test.ts`, `obs-rendering_test.ts`,
    `policy-pages_test.ts`, `popout_test.ts`, `profile_test.ts`, `remote_test.ts`,
    `shared-topbar_test.ts`, `stats_test.ts`, `theater-creation_test.ts`,
    `theater-editor_test.ts`, `theater-report_test.ts`, and `user-help_test.ts`) still
    read raw HTML/JS/assets from disk via `fs.readFileSync` / `fs.promises.readFile`
    instead of using shared DOM/template fixture helpers.
  - Python UI fixture helpers (`testing/ui/conftest.py`, `testing/ui/canvas_source.py`,
    `testing/ui/base.py`) remain required for the remaining Python testlab/route suites.

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
    `allowJs: true` / `checkJs: false` remain temporary until raw JS is eliminated.
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
