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

### Canvas TypeScript Migration

The browser migration is complete. The [migration inventory](frontend/canvas/MIGRATION.md)
records the original Python test baseline, replacement suites, completed waves,
and optional improvements.

- [x] Add strict TypeScript type checking, production build/watch commands, and locked npm dependencies.
- [x] Compile browser modules from `frontend/canvas/` to ignored `static/generated/canvas/` assets for local startup and Docker builds.
- [x] Migrate all authored browser JavaScript, page/testlab scripts, audio-worklet processors, and the sandboxed game-panel bridge to TypeScript (82 production modules).
- [x] Migrate frontend behavior checks to 100 Playwright TypeScript suites (99 beside production sources and `testing/ui/canvas_fixtures_test.ts`).
- [x] Preserve all 102 original core frontend test definitions through behavioral replacements; retain the 2 backend undo definitions in Python.
- [x] Retain backend/API/state suites and Python browser/server integration tests in Python.
- [x] Serve real compiled modules through shared browser fixtures with HTTP/WebSocket isolation and page-error guards.
- [x] Retire the JavaScript compatibility shims, obsolete CJS tests, `test:cjs`, and mixed-JavaScript compiler options. Browser callers use canonical `/static/generated/canvas/` URLs; `static/js/splitting.min.js` remains vendored.
- [x] Document browser installation, full/focused verification commands, and standalone Test Lab builds in the README and testing guide.
- [ ] Optional: consolidate page/API/WebSocket fixtures beyond canvas/game panels.
- [ ] Optional: tighten payload casts and discriminated unions; evaluate `noUncheckedIndexedAccess` and `exactOptionalPropertyTypes` separately.
