---
name: narratron-testing
description: Instructions and guidelines for running unit tests, evaluation testcases, and automated end-to-end testing in Narratron Buddy.
---

# Narratron Buddy Testing Guide

This skill provides comprehensive instructions for running and maintaining tests in Narratron Buddy, covering Python unit and integration tests, frontend TypeScript Playwright tests, Node CJS test suites, test layout conventions, offline network guards, and automated end-to-end narration evaluation.

## 1. Testing Strategy & Naming Conventions

### File Location & Naming

Tests are organized into two primary categories:

1. **Python Unit Tests**: Follow the `*_test.py` naming convention and are placed **directly adjacent** to the modules they test.
   - **Tools**:
     - `tools/image/image_tool_test.py` (tests `tools/image/image_tool.py`)
     - `tools/music_tool_test.py` (tests `tools/music_tool.py`)
     - `tools/notes_tool_test.py` (tests `tools/notes_tool.py`)
     - `tools/chat_tool_test.py` (tests `tools/chat_tool.py`)
   - **Components**:
     - `components/canvas_state_test.py` (tests `components/canvas_state.py`)
   - **Utilities**:
     - `utils/config_loader_test.py` (tests `utils/config_loader.py`)
     - `utils/image_utils_test.py` (tests `utils/image_utils.py`)
     - `utils/frontend_build_test.py` (tests `utils/frontend_build.py`)
   - **Services**:
     - `services/live_stream_service_test.py` (tests `services/live_stream_service.py`)

2. **Frontend TypeScript Tests**:
   - Placed **directly adjacent** to their source files in `frontend/canvas/` using the `*_test.ts` convention:
     - `frontend/canvas/cursor-state_test.ts` (tests `frontend/canvas/cursor-state.ts`)
     - `frontend/canvas/bookmarks_test.ts` (tests `frontend/canvas/bookmarks.ts`)
     - `frontend/canvas/navigation_test.ts` (tests `frontend/canvas/navigation.ts`)
     - `frontend/canvas/doodle-actions_test.ts` (tests `frontend/canvas/doodle-actions.ts`)
   - Shared browser test fixtures and their self-tests live in `testing/ui/`:
     - `testing/ui/canvas_fixtures.ts` (shared Playwright fixture)
     - `testing/ui/canvas_fixtures_test.ts` (self-tests for `canvas_fixtures.ts`)

3. **Node CJS Tests & Legacy Python UI Tests (`testing/ui/`)**:
   - **Node CJS Tests (`*_test.cjs`)**: Isolated Node.js test runner suites for standalone frontend modules.
     - `testing/ui/audio_recorder_test.cjs`
     - `testing/ui/canvas_state_polling_test.cjs`
     - `testing/ui/story_log_inspector_test.cjs`
   - **Python UI Tests (`*_test.py`)**: Legacy and backend-focused UI integration tests (e.g. backend undo handlers in `canvas_undo_test.py`, corner controls, zoom effects).

---

### Running Tests

#### Frontend TypeScript & UI Test Suites (`npm`)

Always use the npm scripts defined in `package.json`:

```bash
# Run the complete frontend verification suite (typecheck + build + test:ui + test:cjs)
npm test

# Run only Playwright TypeScript UI tests (*_test.ts)
npm run test:ui
# Or run with playwright flags directly:
npx playwright test
npx playwright test frontend/canvas/cursor-state_test.ts
npx playwright test --workers=4
npx playwright test -g "cursor-state"

# Run TypeScript type-checking across project & tests (tsc --noEmit)
npm run typecheck

# Build production TypeScript modules (frontend/canvas/ -> static/generated/canvas/)
npm run build

# Watch mode for interactive TypeScript development
npm run watch

# Run Node CJS test suites (*_test.cjs)
npm run test:cjs
```

#### Python Unit Tests (`uv run pytest`)

Always invoke pytest via `uv run pytest`. The test runner is configured with `pytest-xdist` (`addopts = -n auto` in `pytest.ini`) to execute tests in parallel across available CPU workers by default:

```bash
# Run the full test suite in parallel (auto-detects CPU count)
uv run pytest

# Run a specific directory or file
uv run pytest services/
uv run pytest tools/image/image_tool_test.py
uv run pytest testing/ui/canvas_undo_test.py

# Run serially without worker processes (useful for step debugging or pdb)
uv run pytest -n 0

# Limit the number of parallel workers
uv run pytest -n 4
```

Parallel execution via `pytest-xdist` significantly reduces full test suite run times (from minutes to under a minute on multi-core systems).

Pytest uses `--import-mode=importlib` so independent adventure packages can reuse test filenames such as `game_test.py` without module-name collisions.

Default discovery (`uv run pytest` with no path) excludes `adventures/` and `theaters/` via `norecursedirs` in `pytest.ini`. The default run covers only the core platform. `theaters/` contains generated copies of adventure files and is never run directly. Adventure tests run only when you pass their path explicitly:

```bash
# Run the tests for the adventure you are working on
uv run pytest adventures/<adventure-slug>

# Run every adventure's tests
uv run pytest adventures
```

- **Changing core platform code**: run `uv run pytest`. Do not add adventure tests to that run.
- **Writing or editing an adventure**: run **only** that adventure's tests with `uv run pytest adventures/<adventure-slug>`. Do not run the full core suite for adventure-only changes.

---

### Incremental Canvas TypeScript Migration & Testing Patterns

When migrating canvas logic from JavaScript to TypeScript:

1. **Source Organization & Compilation**:
   - Production TypeScript source code resides in `frontend/canvas/<module>.ts`.
   - The build compiles modules to browser ES modules in `static/generated/canvas/<module>.js`.
   - `static/generated/` is gitignored and compiled automatically during `Dockerfile` builds and local startup via `utils/frontend_build.py`.
   - Relative imports between TypeScript files in `frontend/canvas/` must use the `.js` extension (e.g., `import { canvasCursorState } from './cursor-state.js';`) to align with native browser ES module resolution.

2. **Backward Compatibility & Shims**:
   - Provide a deprecated re-export shim in `static/js/canvas-<module>.js` that forwards exports to `/static/generated/canvas/<module>.js`.
   - Ensure all callers import via a single canonical URL so singleton module instances remain shared.

3. **Writing Playwright Tests with `canvas_fixtures.ts`**:
   - Use the shared fixture in `testing/ui/canvas_fixtures.ts`:
     ```ts
     import { expect, test } from './canvas_fixtures';

     test('module interaction', async ({ canvas, page }) => {
       await canvas.mount({
         html: '<div id="container"><button id="btn">Click</button></div>',
         stylesheets: ['/static/css/canvas-navigation.css'],
       });

       const result = await page.evaluate(async () => {
         const { myModule } = await window.__importModule('/static/generated/canvas/my-module.js');
         return myModule.getState();
       });
       expect(result).toBe(true);
     });
     ```
   - For full canvas page testing, use `canvas.openCanvas({ isOrator: true, ... })` which loads `templates/canvas.html` with mocked backend APIs (`/api/*`, `/theaters/*`, and `wss://...`).
   - Use `window.__importModule(url)` in browser evaluations to prevent bundlers/transpilers from rewriting dynamic browser `import()` into CommonJS `require()`.

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

- **Gemini API / Vertex AI**: Always mock `genai.Client` in `ImageTools` tests using `unittest.mock.patch("tools.image_tool.genai.Client")` to avoid external API calls during unit tests.
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
├── ui/                # Shared browser fixtures & legacy UI integration tests
│   ├── canvas_fixtures.ts        # Shared Playwright TS fixture (offline guard, static server, canvas.html loader)
│   ├── canvas_fixtures_test.ts   # Self-tests for shared Playwright fixtures
│   ├── audio_recorder_test.cjs   # Node CJS test suite for audio recorder
│   ├── canvas_state_polling_test.cjs # Node CJS test suite for state polling
│   ├── story_log_inspector_test.cjs  # Node CJS test suite for story log inspector
│   ├── canvas_undo_test.py       # Python backend tests for undo endpoints
│   ├── canvas_corner_controls_test.py # Python UI integration tests
│   ├── conftest.py               # Pytest fixtures and frontend build triggers
│   └── base.py                   # Shared unittest Playwright fixture for Python
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
