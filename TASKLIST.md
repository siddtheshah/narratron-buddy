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
  - Keep production TypeScript in `frontend/canvas/`, emit browser ES modules to `static/generated/canvas/`, and keep browser tests in `testing/ui/`.
  - Include generated assets in local startup and deployment builds without overwriting existing JavaScript source files.
  - Support mixed JavaScript/TypeScript with `allowJs` and initially leave `checkJs` disabled; use strict checking for TypeScript and explicit types at migrated module boundaries.
  - Run a separate `tsc --noEmit` check because Playwright transpiles TypeScript tests without checking their types.
- [x] Add shared browser fixtures that serve real production modules and assets.
  - Preserve HTTP/WebSocket isolation, block service workers, and fail on unexpected requests.
  - Replace source slicing and source-text assertions with module imports and behavior checks where practical.
- [x] Pilot the migration with cursor state and its TypeScript tests.
  - Update existing JavaScript callers to import the compiled module, using one consistent URL for shared singleton state.
  - Verify interoperability before expanding the migration; TypeScript tests can also cover modules that are still JavaScript.
- [ ] Migrate bookmarks, doodle actions/undo, navigation, and rendering in small batches, moving their tests alongside the logic.
  - Completed: `bookmarks` (`frontend/canvas/bookmarks.ts`, `frontend/canvas/bookmarks_test.ts`), `doodle actions/undo` (`frontend/canvas/doodle-actions.ts`, `frontend/canvas/doodle-actions_test.ts`, retaining the 2 backend tests in `testing/ui/canvas_undo_test.py`), and `navigation` (`frontend/canvas/navigation.ts`, `frontend/canvas/navigation_test.ts`). Rendering implementation is now TypeScript; remaining rendering tests still need migration.
  - Image rendering implementation: `frontend/canvas/image-renderer.ts` now owns transitions, sequences, layered animation, video playback, and CDN policy with explicit typed options and controllers. `static/js/canvas-renderers.js` re-exports its public API alongside the legacy doodle renderer. Five additional browser tests cover sequence pixels/lifecycle, mesh/layer composition, video silence/loop/fallback/cancellation, and compatibility.
  - Doodle rendering implementation: `frontend/canvas/doodle-renderer.ts` and `doodle-types.ts` define typed annotations, DOM nodes, selection, pointer interactions, stamp transforms, text rendering, and geometry. The renderer compatibility module re-exports both typed factories; remaining Python snippet fixtures read the compiled doodle module.
  - Shared animation dispatch: `frontend/canvas/media.ts` defines typed triframe/layered/video manifests and preserves signature, URL resolution, defaults, and renderer option forwarding. Canvas and OBS use the compiled module; the legacy media module re-exports it.
  - Rendering test parity: `frontend/canvas/renderers_test.ts` replaces `canvas_geometry_test.py` and `canvas_zoom_effect_test.py` (2 Python test definitions, 6 parametrized cases), importing real modules and CSS through the shared offline fixture. Preserves contain/cover, DPR=2, rotated stamps, canonical text sizing, segment hit testing, pointer normalization, zoom, pan, resize, and image-effect alignment.
- [ ] Continue with asset-manager behavior, canvas controls, and A2UI surfaces; extract smaller typed modules from `static/js/canvas.js` as needed.
- [ ] Track coverage parity for the initial core: 17 Python files and 104 test definitions before parametrization (`canvas*_test.py` and `a2ui_canvas_drag_test.py`).
  - Coverage inventory: `frontend/canvas/MIGRATION.md` records all 17 baseline files and their replacements. Current disposition: 19 frontend definitions migrated, 83 pending, 2 backend definitions retained.
  - Retain the two Python backend tests currently in `canvas_undo_test.py`, leaving 102 frontend migration candidates.
  - Preserve parametrized cases, permissions, persistence, geometry, and race-condition coverage; remove superseded Python frontend tests only after their replacements pass.
  - Completed state polling: `frontend/canvas/state-polling.ts` and 13 browser tests (all 12 former CJS cases plus module-shim compatibility); canvas, OBS, and popout import the compiled module directly.
  - Retain and integrate existing JavaScript story-log inspector and audio-recorder tests while migrating them to TypeScript progressively.
- [ ] Follow with adjacent canvas suites, including action wheel, doodle history, doodles, and hotkey configuration.
  - Scope remote, OBS, game UI, and live load/evaluation harnesses separately; keep backend state/API tests in Python.

