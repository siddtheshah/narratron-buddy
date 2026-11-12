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
  - Completed: `bookmarks` (`frontend/canvas/bookmarks.ts`, `frontend/canvas/bookmarks_test.ts`), `doodle actions/undo` (`frontend/canvas/doodle-actions.ts`, `frontend/canvas/doodle-actions_test.ts`, retaining the 2 backend tests in `testing/ui/canvas_undo_test.py`), and `navigation` (`frontend/canvas/navigation.ts`, `frontend/canvas/navigation_test.ts`). Remaining: `rendering`.
- [ ] Continue with asset-manager behavior, canvas controls, and A2UI surfaces; extract smaller typed modules from `static/js/canvas.js` as needed.
- [ ] Track coverage parity for the initial core: 17 Python files and 104 test definitions before parametrization (`canvas*_test.py` and `a2ui_canvas_drag_test.py`).
  - Retain the two Python backend tests currently in `canvas_undo_test.py`, leaving 102 frontend migration candidates.
  - Preserve parametrized cases, permissions, persistence, geometry, and race-condition coverage; remove superseded Python frontend tests only after their replacements pass.
  - Retain and integrate existing JavaScript polling, story-log inspector, and audio-recorder tests while migrating them to TypeScript progressively.
- [ ] Follow with adjacent canvas suites, including action wheel, doodle history, doodles, and hotkey configuration.
  - Scope remote, OBS, game UI, and live load/evaluation harnesses separately; keep backend state/API tests in Python.

