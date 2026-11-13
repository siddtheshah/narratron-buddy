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
definitions remain in Python (`testing/ui/canvas_undo_test.py`). All 24 production
TypeScript modules in `frontend/canvas/` — the 19 core canvas modules
(`a2ui-renderer.ts`, `agent-activity.ts`, `asset-manager.ts`, `bookmarks.ts`,
`collab-bar.ts`, `cursor-state.ts`, `doodle-actions.ts`, `doodle-renderer.ts`,
`doodle-types.ts`, `image-renderer.ts`, `layout-resize.ts`, `media.ts`,
`navigation.ts`, `pin-control.ts`, `scene.ts`, `speech-replay.ts`, `stamps.ts`,
`state-polling.ts`, and `text-annotations.ts`) plus 5 adjacent Wave-2 modules
(`action-wheel.ts`, `audio-recorder.ts`, `beyond20-listener.ts`,
`device-aware-pcm.ts`, and `story-log-inspector.ts`) — compile to
`static/generated/canvas/`, with re-export shims preserved in `static/js/` where
legacy/OBS/remote callers import them. Corner-controls and chat-name wiring
remain in `static/js/canvas.js` and `static/js/chat-controller.js` and are
covered by `corner-controls_test.ts` and `chat-name_test.ts`. The 4 former
Python renderer snippet tests (`doodles_test.py`, `doodle_history_test.py`,
`hide_canvas_text_test.py`, and `sticky_notes_test.py`) have moved to
TypeScript in `frontend/canvas/`; only `testing/ui/image_history_paging_test.py`
and `testing/ui/remote_test.py` still read compiled output directly
(`static/generated/canvas/story-log-inspector.js`).

Additional coverage outside this baseline (16 additional `frontend/canvas/*_test.ts`
suites beyond the 20 baseline-derived suites, plus `testing/ui/canvas_fixtures_test.ts`):

- `accepted-suggestions_test.ts`: replaces `accepted_suggestions_test.py`.
- `action-wheel_test.ts`: replaces `action_wheel_test.py` and covers `action-wheel.ts`.
- `audio-recorder_test.ts`: replaces `audio_recorder_test.cjs` and covers `audio-recorder.ts`.
- `beyond20-listener_test.ts`: replaces `beyond20_listener_test.py` and covers `beyond20-listener.ts`.
- `chat-composer_test.ts`: replaces `chat_composer_test.py`.
- `chat-highlight_test.ts`: replaces `chat_highlight_test.py`.
- `cursor-state_test.ts`: store validation, subscription, interoperability, and real canvas controls.
- `doodle-history_test.ts`: replaces `doodle_history_test.py`.
- `doodles_test.ts`: replaces `doodles_test.py`.
- `hide-canvas-text_test.ts`: replaces `hide_canvas_text_test.py`.
- `hotkey-config_test.ts`: replaces `hotkey_config_test.py`.
- `image-renderer_test.ts`: CDN policy, shim identity, sequence pixels and lifecycle,
  layered composition/effects, silent video fallback and looping, and load cancellation.
- `media_test.ts`: manifest signatures, URL resolution, timing defaults, option forwarding,
  input immutability, and legacy shim identity.
- `microphone-test_test.ts`: replaces `microphone_test_test.py` and covers `device-aware-pcm.ts`.
- `minimal-mode_test.ts`: replaces `minimal_mode_ui_test.py`.
- `music-mute-persistence_test.ts`: replaces `music_mute_persistence_test.py`.
- `state-polling_test.ts`: all 12 original CJS cases plus module-shim compatibility.
- `sticky-notes_test.ts`: replaces `sticky_notes_test.py`.
- `story-log-inspector_test.ts`: replaces `story_log_inspector_test.cjs` and covers `story-log-inspector.ts`.
- `suggestions_test.ts`: replaces `suggestions_test.py`.
- `testing/ui/canvas_fixtures_test.ts`: asset serving, request isolation, service-worker
  blocking, secondary contexts, and full canvas mounting.
- `testing/ui/triframe_animation_test.py` and `video_animation_ui_test.py` retain
  backend manifest/path/history checks.

Verification: `npm test` runs strict type checking (`npm run typecheck`), the
production build (`npm run build`), and all TypeScript Playwright tests
(`npm run test:ui`; 37 test files). Both legacy CommonJS suites are migrated and
deleted, and `test:cjs` has been removed from `package.json`. Run Python checks
with `uv run pytest`; generated modules are built by the UI fixture when stale.

## Repository-Wide Migration Status (Beyond the Canvas Core)

While the 17-file / 102-definition Canvas core baseline above is complete, the full
repository migration (no raw JS, all frontend tests in TypeScript with shared TS
fixtures) remains incomplete:

### Remaining Raw JavaScript

- **`static/js/` (22 non-shim files, 14,668 lines)**:
  - Canvas orchestration still in JS: `canvas.js` (5,645 lines)
  - Remote controls: `remote.js` (1,691), `remote-audio.js` (137), `remote-game.js` (52), `remote-login.js` (8)
  - Effects: `image-effects.js` (1,644), `animation-effects.js` (799)
  - Theater builder & reporting: `theater-editor.js` (1,502), `theater-report.js` (101)
  - Chat: `chat-controller.js` (795), `chat-emotes.js` (69)
  - Games: `game-renderer.js` (567), `game-runtime.js` (188), `game-panel.js` (130)
  - Auth, navigation, & shared UI: `auth-flow.js` (531), `credit-purchase.js` (285), `friends.js` (211), `user-help.js` (150), `docs-navigation.js` (79), `narration-pane.js` (32), `device-navigation.js` (22), and vendored `splitting.min.js` (30).
- **`static/js/` (14 re-export shims: 13 `@deprecated`, plus `canvas-renderers.js`)**:
  `a2ui-canvas-renderer.js`, `action-wheel.js`, `audio-recorder.js`,
  `beyond20-listener.js`, `canvas-bookmarks.js`, `canvas-cursor-state.js`,
  `canvas-doodle-actions.js`, `canvas-media.js`, `canvas-navigation.js`,
  `canvas-renderers.js` (lacks `@deprecated` JSDoc), `canvas-scene.js`,
  `canvas-state-polling.js`, `device-aware-pcm.js`, and `story-log-inspector.js`
  remain for legacy/template imports.
- **Other raw JS files**: `testlab/static/audio-vad-lab.js` (297 lines) and
  `scripts/build_canvas.mjs` (73 lines). Both CommonJS test files
  (`testing/ui/audio_recorder_test.cjs` and `testing/ui/story_log_inspector_test.cjs`)
  have been migrated to TypeScript and deleted.
- **Inline `<script>` blocks in HTML**:
  - 9 templates in `templates/`: `theater_creation.html` (746 lines), `obs.html` (632),
    `popout.html` (449), `adventures.html` (183), `stats.html` (154), `join_splash.html`
    (126), `profile.html` (73), `shared_topbar.html` (32), and `gift.html` (23).
  - 14 lab HTML pages in `testlab/` (including `adventure_runner.html` at 756 lines,
    `decision_lab.html` at 633, `layered_animation_lab.html` at 543,
    `animation_benchmark.html` at 300, `game_lab.html` at 291, `text_effects_lab.html`
    at 236, and `video_benchmark.html` at 229).
- **Compiler/build scope**: `tsconfig.json` keeps `"allowJs": true` and `"checkJs": false`
  (with `"/static/js/*"` mapped in `paths`), and `tsconfig.build.json` /
  `scripts/build_canvas.mjs` only emit `frontend/canvas/` -> `static/generated/canvas/`.

### Frontend Tests Still in Python (`testing/ui/`)

Of 33 `*_test.py` files (and 0 `*_test.cjs` files) in `testing/ui/`:

- **17 pure-frontend Playwright tests in Python**:
  `auth_password_reset_test.py`, `auth_signup_test.py`, `friends_test.py`,
  `game_canvas_stamp_test.py`, `game_form_test.py`, `game_panel_test.py`,
  `game_renderer_test.py` (also invokes `node` subprocesses), `game_runtime_test.py`,
  `join_splash_test.py`, `network_test.py` (exercises Python's `conftest.py` firewall),
  `obs_rendering_test.py`, `profile_stamps_test.py`, `theater_creation_test.py`,
  `theater_editor_test.py`, `theater_report_test.py`, `user_help_chat_test.py`, and
  `user_help_markdown_test.py`.
- **1 pure-frontend source-assertion test in Python**:
  `auth_state_test.py`.
- **11 mixed backend + frontend tests in Python** (FastAPI/`CanvasStateManager` checks
  combined with template/JS assertions or Playwright):
  `adventure_guide_ui_test.py`, `adventure_runner_game_test.py`, `crossfade_test.py`,
  `fullscreen_popout_test.py`, `game_lab_test.py`, `image_history_paging_test.py`,
  `obs_canvas_test.py`, `policy_pages_test.py`, `remote_test.py` (mostly
  Playwright phone-viewport UI), `triframe_animation_test.py`, and
  `video_animation_ui_test.py`. Split frontend assertions into TypeScript and keep only
  backend state/route tests in Python.
- **0 Node `vm` CommonJS suites**: `audio_recorder_test.cjs` and
  `story_log_inspector_test.cjs` were migrated to `frontend/canvas/audio-recorder_test.ts`
  and `frontend/canvas/story-log-inspector_test.ts` and deleted.
- **4 pure Python/backend suites to retain in Python**: `agent_credits_test.py`,
  `canvas_undo_test.py`, `chat_prefix_test.py`, and `load_canvas_drawing_test.py`.

### TypeScript Testing Fixtures Status

- **Existing fixture (`testing/ui/canvas_fixtures.ts`)**: Used by all 36
  `frontend/canvas/*_test.ts` suites (plus `testing/ui/canvas_fixtures_test.ts`);
  enforces offline HTTP/WebSocket isolation, blocks service workers, serves `/static/`
  assets, and provides `mount()` and `openCanvas()`.
- **Gaps**:
  - Fixtures only target `/canvas`; there are no shared TypeScript page/API/WebSocket
    fixtures yet for `obs`, `popout`, `remote`, `theater-editor`, `theater-creation`,
    `profile`, `join-splash`, `friends`, `game-renderer`/`game-panel`, or `testlab`.
  - 11 TypeScript suites (`action-wheel_test.ts`, `agent-activity_test.ts`,
    `asset-manager_test.ts`, `beyond20-listener_test.ts`, `chat-composer_test.ts`,
    `chat-highlight_test.ts`, `chat-name_test.ts` [also reads `static/js/canvas.js`],
    `collab-bar_test.ts`, `corner-controls_test.ts`, `doodles_test.ts`, and
    `navigation_test.ts`) still read raw HTML/JS from disk via `fs.readFileSync` /
    `fs.promises.readFile` instead of using shared DOM/template fixture helpers.
  - Python UI fixture helpers (`testing/ui/conftest.py`, `testing/ui/canvas_source.py`,
    `testing/ui/base.py`) remain required until the Python frontend tests are migrated.

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
  - Legacy JS dependencies are typed via declaration files (`animation-effects.d.ts`,
    `image-effects.d.ts`) rather than `any`.
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
  - Several `frontend/canvas/*_test.ts` files use `window as unknown as <HarnessWindow>`
    in `page.evaluate(...)`, `{ ... } as unknown as Response` fetch stubs in
    `stamps_test.ts` (prefer `new Response(...)`), and `undefined as unknown as CanvasAssetManager`
    in `asset-manager_test.ts` (prefer `CanvasAssetManager | null`).
