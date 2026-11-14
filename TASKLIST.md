# Narratron Task List


## Auth, Security & Monetization
- [ ] Add rate limits for registration/login, password reset, join-key resolution, uploads, payment attempts, and Live WebSocket connections.
- [ ] Use secure production auth cookies and CSRF protection for authenticated state-changing endpoints.
- [ ] Require authenticated theater ownership (or the active baton holder where appropriate) for agent start, stop, and status endpoints.
  - The current lifecycle endpoints accept a theater ID without checking the caller's identity or authorization.
- [ ] Cloudflare

## Package & Dependency Updates
- [ ] **High-Priority Python Security Updates (`uv.lock`):**
  - [x] `authlib` `1.8.0` -> `1.9.5` (updated to `1.9.6`; `CVE-2026-114616`..`114619`: zero-len HMAC, OIDC nonce bypass, JWT `aud`, scope validation).
  - [x] `pillow` `12.3.0` -> `13.0.0` (`CVE-2026-112900` Ghostscript RCE, `CVE-2026-96639` OOB write, TIFF DoS; requires Python `>=3.11`).
  - [x] `grpcio` `1.84.0` -> `1.85.1` (`GHSA-2xm2-c8xx-p9vh`: remove compromised eMudhra/emSign CAs from `roots.pem`).
  - [x] `certifi` `2026.7.22` -> `2026.10.23` (remove compromised eMudhra/emSign CA roots).
  - [x] `aiohttp` `3.14.3` -> `3.14.5` (`CVE-2026-128126` & `CVE-2026-128130` DoS/memory exhaustion).
  - [ ] `websockets` `15.0.1` -> `17.4` (blocked by `google-adk<16`, `openai[realtime]<16`, and `google-genai<17.0` pins; cap fragmented message frame count to prevent memory-exhaustion DoS; verify `uvicorn` compatibility).
  - [x] `joserfc` `1.7.5` -> `1.8.0` (`CVE-2026-119284` Compact JWS DoS + JWK/JWS/JWE hardening; requires Python `>=3.11`).
  - [x] `pyarrow` `25.0.1` -> `26.0.1` (`CVE-2026-92287`: Parquet KMS client SSRF / credential leak).
  - [x] `sqlparse` `0.6.0` -> `0.6.1` (`CVE-2026-112307` & `GHSA-3r5h-5jcg-gmgg` DoS; final EOL release — plan migration off `sqlparse`).
- [x] **Dockerfile & Runtime Image Updates:**
  - [x] Upgrade `golang:1.25-bookworm` (EOL Aug 2026) -> `golang:1.26+` (`golang:1.26-bookworm`; `game_engine/cel_go_worker` build verified).
  - [x] Upgrade `ghcr.io/astral-sh/uv:0.6.5` -> `>=0.11.6` (`0.11.21`; `CVE-2025-54368`, `CVE-2026-114110`).
  - [x] Run production container as a non-root user instead of `root` (`appuser`, UID 10001).
- [x] **Manifest & Build Hygiene (`pyproject.toml`, `requirements*.txt`):**
  - [x] Raise `requires-python` from `>=3.10` (EOL Oct 2026) to `>=3.11` or `>=3.13` (`>=3.13`, matching `Dockerfile` `python:3.13-slim`) to unblock `pillow>=13.0.0` and `joserfc>=1.8.0`.
  - [x] Move `playwright`, `pytest`, `pytest-asyncio`, and `pytest-xdist` from main `dependencies` to `[dependency-groups] dev`.
  - [x] Sync `requirements.txt` and `requirements-dev.txt` with `pyproject.toml` (`aiosqlite`, `sqlalchemy`, `pytest-env`, `pytest-socket`, and `google-adk[db]`/`openai[realtime]` extras).

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
