---
name: narratron-testing
description: Instructions and guidelines for running unit tests, evaluation testcases, and automated end-to-end testing in Narratron Buddy.
---

# Narratron Buddy Testing Guide

This skill provides comprehensive instructions for running and maintaining tests in Narratron Buddy, covering Python unit and integration tests, frontend TypeScript Playwright tests, test layout conventions, offline network guards, and automated end-to-end narration evaluation.

## 1. Testing Strategy & Naming Conventions

### File Location & Naming

Tests are organized into three categories:

1. **Python Unit Tests**: Follow the `*_test.py` naming convention and are placed **directly adjacent** to the modules they test.
   - **Tools**:
     - `tools/image/image_tool_test.py` (tests `tools/image/image_tool.py`)
     - `tools/music_tool_test.py` (tests `tools/music_tool.py`)
     - `tools/notepad_tool_test.py` (tests `tools/notepad_tool.py`)
     - `tools/chat_tool_test.py` (tests `tools/chat_tool.py`)
   - **Components**:
     - `components/canvas/canvas_state_manager_test.py` (tests `components/canvas/canvas_state_manager.py`)
   - **Utilities**:
     - `utils/config_loader_test.py` (tests `utils/config_loader.py`)
     - `utils/image_utils_test.py` (tests `utils/image_utils.py`)
     - `utils/frontend_build_test.py` (tests `utils/frontend_build.py`)
   - **Services**:
     - `services/live_stream_service_test.py` (tests `services/live_stream_service.py`)

2. **Frontend TypeScript Tests**:
   - Placed **directly adjacent** to their source files under `frontend/` using the `*_test.ts` convention:
     - `frontend/canvas/cursor-state_test.ts` (tests `frontend/canvas/cursor-state.ts`)
     - `frontend/canvas/bookmarks_test.ts` (tests `frontend/canvas/bookmarks.ts`)
     - `frontend/canvas/navigation_test.ts` (tests `frontend/canvas/navigation.ts`)
     - `frontend/canvas/doodle-actions_test.ts` (tests `frontend/canvas/doodle-actions.ts`)
     - `frontend/shared/state-polling_test.ts` (tests `frontend/shared/state-polling.ts`)
   - Shared browser test fixtures and their self-tests live in `testing/ui/`:
     - `testing/ui/canvas_fixtures.ts` (shared Playwright fixture)
     - `testing/ui/canvas_fixtures_test.ts` (self-tests for `canvas_fixtures.ts`)

3. **Python UI & Backend Integration Tests (`testing/ui/`)**:
   - **Python UI Tests (`*_test.py`)**: Backend-focused UI and API integration tests (e.g., backend undo handlers in `canvas_undo_test.py`, FastAPI/baton-credits checks in `remote_test.py`, and manifest/state checks in `triframe_animation_test.py`).

---

### Running Tests

#### CPU & Worker Limits

**Never use all available CPU cores when running tests.** Leave CPU headroom so the host machine and concurrent tools or agents stay responsive:

- **Stay at or below half of available CPU cores** (default cap: `4` workers).
- **Never run uncapped parallelism**: Do not override `pytest` with uncapped `-n auto` or `-n logical`, and do not pass a Playwright `--workers` count equal to the machine's total CPU count.
- **Defaults are bounded**: `pytest.ini` caps default `pytest-xdist` workers with `--maxprocesses=4`, and `playwright.config.ts` caps default Playwright workers at `Math.max(1, Math.min(4, Math.floor(os.cpus().length / 2)))`. On machines with fewer than 8 cores, pass an explicit lower `-n` count (for example `-n 2`) so `pytest` also stays at or below half of available cores.
- **Prefer serial execution for small targeted runs**: Use `uv run pytest -n 0 <path>` and `--workers=1` for single-file or small targeted runs to avoid worker startup overhead and unnecessary CPU spikes.
- **Scale down under concurrency**: When multiple agents or heavy workloads are running in parallel, use lower caps (such as `-n 0` or `-n 2` for `pytest`, and `--workers=1` or `--workers=2` for Playwright).

#### Swarm Worker vs. Coordinator Test Scope

When working in a multi-agent swarm:

- **Swarm workers** must **not** run the full test suite (`npm test`, unfiltered `npm run test:ui` / `npx playwright test`, or unfiltered `uv run pytest`) on their own. Running full suites across multiple parallel workers causes heavy CPU/memory contention and slows down execution.
- **Swarm workers** should run **only targeted checks** with low/serial worker counts for the modules and files they own or touched, for example:
  - `npm run typecheck`
  - `npm run build`
  - `npx playwright test frontend/canvas/<specific>_test.ts --workers=1`
  - `uv run pytest -n 0 <specific_test_file.py>` (or `-n 2` for a larger directory)
  - `uv run ruff check <touched_py_files>`
- **The coordinator** (or a post-merge integration check) is responsible for running the full test suite with bounded workers (`npm test` and `uv run pytest -n 4`) when appropriate, such as after integrating wave branches.

#### Frontend TypeScript & UI Test Suites (`npm`)

Always use the npm scripts defined in `package.json`. `playwright.config.ts` caps default workers at half of available CPU cores (maximum 4) so default runs do not saturate the CPU:

```bash
# Install locked dependencies and Chromium (once per checkout/environment)
npm ci
npx playwright install chromium
uv sync --locked
uv run python -m playwright install chromium

# Run the complete frontend verification suite (typecheck + build + test:ui, bounded workers)
npm test

# Build first when running UI tests separately; Playwright does not build assets
npm run build
# Run only Playwright TypeScript UI tests (*_test.ts, bounded workers by default)
npm run test:ui
# Forward filters and explicit worker limits to Playwright through this script:
npm run test:ui -- frontend/canvas/cursor-state_test.ts --workers=1
npm run test:ui -- --workers=4
# Or run with playwright flags directly:
npx playwright test
npx playwright test frontend/canvas/cursor-state_test.ts --workers=1
npx playwright test --workers=4
npx playwright test -g "cursor-state" --workers=1

# Run TypeScript type-checking across project & tests (tsc --noEmit)
npm run typecheck

# Build production TypeScript modules (frontend/ -> static/generated/)
npm run build

# Watch mode for interactive TypeScript development
npm run watch
```

#### Python Unit Tests (`uv run pytest`)

Always invoke pytest via `uv run pytest`. The test runner is configured with `pytest-xdist` (`addopts = -n auto --maxprocesses=4` in `pytest.ini`) so parallel runs stay bounded instead of consuming all available CPU cores. Avoid uncapped `-n auto` or `-n logical`, and prefer `-n 0` for single-file or small targeted runs:

```bash
# Run the full test suite with bounded parallel workers (at most half of CPU cores; max 4)
uv run pytest -n 4

# Run a specific directory or file (prefer -n 0 for single files to avoid worker startup overhead)
uv run pytest -n 2 services/
uv run pytest -n 0 tools/image/image_tool_test.py
uv run pytest -n 0 testing/ui/canvas_undo_test.py

# Run serially without worker processes (useful for targeted runs, step debugging, or pdb)
uv run pytest -n 0
```

Bounded parallel execution via `pytest-xdist` keeps full test suite run times short without starving the host CPU.

Pytest uses `--import-mode=importlib` so independent adventure packages can reuse test filenames such as `game_test.py` without module-name collisions.

Default discovery (`uv run pytest` with no path) excludes `adventures/` and `theaters/` via `norecursedirs` in `pytest.ini`. The default run covers only the core platform. `theaters/` contains generated copies of adventure files and is never run directly. Adventure tests run only when you pass their path explicitly:

```bash
# Run the tests for the adventure you are working on
uv run pytest -n 0 adventures/<adventure-slug>

# Run every adventure's tests (bounded workers)
uv run pytest -n 4 adventures
```

- **Changing core platform code**: run `uv run pytest -n 4` (or fewer workers on machines with fewer than 8 cores). Do not add adventure tests to that run.
- **Writing or editing an adventure**: run **only** that adventure's tests with `uv run pytest -n 0 adventures/<adventure-slug>`. Do not run the full core suite for adventure-only changes.

---

### Canvas TypeScript Layout & Testing Patterns

See [`frontend/README.md`](../../frontend/README.md) for the current browser
module layout. Follow these patterns for new modules and tests:

1. **Source Organization & Compilation**:
   - Production TypeScript source code resides in `frontend/<consumer>/<module>.ts`.
   - The build compiles modules to browser ES modules in `static/generated/<consumer>/<module>.js`.
   - `static/generated/` is gitignored and compiled automatically during `Dockerfile` builds and local startup via `utils/frontend_build.py`.
   - Relative imports between TypeScript files under `frontend/` must use the `.js` extension (e.g., `import { canvasCursorState } from './cursor-state.js';`) to align with native browser ES module resolution.

2. **Canonical Module Imports**:
   - Import migrated modules directly via their canonical compiled URL (`/static/generated/<consumer>/<module>.js`).
   - Ensure all templates, modules, and tests import via the same canonical URL so singleton module instances remain shared (compatibility re-export shims under `static/js/` have been retired).

3. **Writing Playwright Tests with `canvas_fixtures.ts`**:
   - Use the shared fixture in `testing/ui/canvas_fixtures.ts`:
     ```ts
     // From frontend/<consumer>/<module>_test.ts:
     import { expect, test } from '../../testing/ui/canvas_fixtures';

     test('module interaction', async ({ canvas, page }) => {
       await canvas.mount({
         html: '<div id="container"><button id="btn">Click</button></div>',
         stylesheets: ['/static/css/canvas-navigation.css'],
       });

       const result = await page.evaluate(async () => {
         const { myModule } = await window.__importModule<typeof import('./my-module.js')>(
           '/static/generated/canvas/my-module.js',
         );
         return myModule.getState();
       });
       expect(result).toBe(true);
     });
     ```
   - For full canvas page testing, use `canvas.openCanvas({ isOrator: true, ... })` which loads `templates/canvas.html` with mocked backend APIs (`/api/*`, `/theaters/*`, and `wss://...`).
   - Use `window.__importModule<typeof import('./my-module.js')>(url)` in browser evaluations for typed access without rewriting dynamic browser `import()` into CommonJS `require()`.

4. **Test Migration & Parity Verification**:
   - Replace regex/string source-slicing assertions with behavioral browser assertions against real modules.
   - Retain backend Python tests (e.g., server websocket handling or persistent database state) in `*_test.py`.
   - Do NOT remove superseded Python frontend tests until TypeScript replacement tests achieve full behavioral parity and pass verification.

---

### Mocking Practices & Offline Network Isolation

#### Pytest Python Isolation
Pytest enforces offline Python networking by default with `pytest-socket`. TCP/UDP sockets and DNS lookups are blocked, including during application imports and collection. Unix domain sockets remain available for local event-loop communication. Fix missing mocks at the service boundary; do not add `enable_socket` markers, `socket_enabled` fixtures, host allowlists, or `--force-enable-socket` to make tests pass. In-process ASGI transports need no network access.

Windows asyncio uses a loopback TCP socket pair for its wakeup pipe. The test harness supplies this connected pair without lifting the guard for other threads or allowing ordinary TCP sockets.

`pytest-env` sets `PYTHON_DOTENV_DISABLED=1` before collection so local `.env` files cannot silently configure live integrations. Install development dependencies with `uv sync` before running tests; these plugins are required.

Test startup removes inherited integration credentials and isolates gcloud's credential directory. Environment and runtime flag changes are restored after each test. Shared application services and default storage constructors use fresh test-owned directories; the registry database and canvas-state cache are reset per test. Import-time text providers are replaced only while the registry is constructed, so provider unit tests continue to exercise real factories with explicit mocks. The planner fixture joins background workers before removing runner mocks. Tests that only inspect cooldown scheduling mock timer creation so callbacks cannot run after their temporary files have been removed.

- **Gemini API / Vertex AI**: Mock clients at the provider boundary used by the code under test (for example, `unittest.mock.patch("providers.gemini_image_provider.genai.Client")`). Configure the complete expected interface and keep external calls offline.
- **FileSystem & Time**: Use `tempfile.mkdtemp()` for isolated output directories and clean up in `tearDown()`.
- **Callbacks**: Use `unittest.mock.MagicMock()` for `on_show_image`, `on_play_playlist`, `on_pause_playlist`, `on_resume_playlist`, and `on_send_chat_message`.

#### Playwright UI Isolation (`canvas_fixtures.ts`)
UI tests have strict offline and security guards:
- **Offline HTTP/WebSocket Guard**: All unexpected/unmocked HTTP requests are aborted (`blockedbyclient`) and WebSocket connections are closed. Unmocked requests record an error and fail test teardown.
- **Service Workers Blocked**: Service workers are blocked (`serviceWorkers: 'block'`) to ensure pristine network routing.
- **Static Asset Serving**: Production `/static/**` files (including `/static/generated/canvas/**`) are served directly from disk on `https://canvas.test`. Missing static file requests cause test teardown failure.
- **Page Error Guard**: Tests fail teardown on uncaught browser `pageerror`s unless explicitly opted out via `canvas.allowPageErrors()`.

---

## 2. Directory Layout of `testing/`

The `testing/` directory contains UI integration tests, test data, evaluation scenarios, and shared test utilities:

```text
testing/
├── e2e/               # End-to-end browser validation and load harnesses
├── ui/                # Shared browser fixtures & backend-focused UI integration tests
│   ├── canvas_fixtures.ts        # Shared Playwright TS fixture (offline guard, static server, canvas.html loader)
│   ├── canvas_fixtures_test.ts   # Self-tests for shared Playwright fixtures
│   ├── canvas_undo_test.py       # Python backend tests for undo endpoints
│   ├── remote_test.py            # Backend FastAPI/baton-credits checks
│   ├── triframe_animation_test.py # Backend animation manifest/state checks
│   ├── conftest.py               # Pytest fixtures and frontend build triggers
│   └── base.py                   # Shared unittest state/storage fixture for Python
├── testcases/         # Evaluation test scenarios containing expectations.json and audio files
│   └── desert_basic/
│       ├── expectations.json
│       └── narration.wav
├── testdata/          # Preloaded test artifacts, sample images, and test sound files
│   ├── images/
│   └── playlists/
└── base.py            # Shared unittest fixture
```

---

## 3. End-to-End Narration Evaluation (`testing/e2e/validate_single_image_generation.py`)

### Overview
`validate_single_image_generation.py` performs automated end-to-end video evaluation:
1. Transcodes input audio narration to 16kHz mono WAV.
2. Boots `main.py` FastAPI server on a free port with preloaded test artifacts (`USE_IN_MEMORY_ARTIFACTS=1`).
3. Launches a Playwright browser context, opens the Orator Canvas (`http://127.0.0.1:<port>/?role=orator`), and records WebM video.
4. Streams narration audio chunks via WebSocket.
5. Muxes video recording with audio.
6. Evaluates recorded video against `expectations.json` using Gemini video analysis.

### Running an Evaluation Testcase

```bash
uv run python testing/e2e/validate_single_image_generation.py --testcase=testing/testcases/desert_basic --headless=True
```

### Available Command Flags
- `--testcase` (`-t`): Path to input audio narration file OR testcase folder containing `expectations.json` (required).
- `--output` (`-o`): Output MP4 video file path (default: `output/evaluation/eval_result1.mp4`).
- `--port` (`-p`): Server port (default: `0` for auto-allocated free port).
- `--headless`: Run browser headless (`True`/`False`).
- `--buffer` (`-b`): Buffer time in seconds after streaming completes (default: `15`).
- `--use_in_memory_artifacts`: Use `PreloadedInMemoryArtifactService` loaded with `testing/testdata` (default: `True`).

---
