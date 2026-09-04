---
name: narratron-testing
description: Instructions and guidelines for running unit tests, evaluation testcases, and automated end-to-end testing in Narratron Buddy.
---

# Narratron Buddy Testing Guide

This skill provides comprehensive instructions for running and maintaining tests in Narratron Buddy, covering unit tests, test layout conventions, and automated end-to-end narration evaluation.

## 1. Unit Testing Strategy & Naming Conventions

### File Location & Naming
Unit tests follow the `*_test.py` naming convention and are placed **directly adjacent** to the modules they test.

UI integration tests are the exception: place them in `testing/ui/`, also using the `*_test.py` convention.

- **Tools**:
  - `tools/image_tool_test.py` (tests `tools/image_tool.py`)
  - `tools/music_tool_test.py` (tests `tools/music_tool.py`)
  - `tools/notes_tool_test.py` (tests `tools/notes_tool.py`)
  - `tools/chat_tool_test.py` (tests `tools/chat_tool.py`)
- **Components**:
  - `components/canvas_state_test.py` (tests `components/canvas_state.py`)
- **Utilities**:
  - `utils/config_loader_test.py` (tests `utils/config_loader.py`)
  - `utils/image_utils_test.py` (tests `utils/image_utils.py`)
- **Services**:
  - `services/live_stream_service_test.py` (tests `services/live_stream_service.py`)

### Running Unit Tests
To run all unit tests across the repository:

```bash
pytest --ignore=scratch
```


### Mocking Practices
- **Gemini API / Vertex AI**: Always mock `genai.Client` in `ImageTools` tests using `unittest.mock.patch("tools.image_tool.genai.Client")` to avoid external API calls during unit tests.
- **FileSystem & Time**: Use `tempfile.mkdtemp()` for isolated output directories and clean up in `tearDown()`.
- **Callbacks**: Use `unittest.mock.MagicMock()` for `on_show_image`, `on_play_playlist`, `on_pause_playlist`, `on_resume_playlist`, and `on_send_chat_message`.

---

## 2. Directory Layout of `testing/`

The `testing/` directory contains UI integration tests, test data, evaluation scenarios, and shared test utilities:

```text
testing/
├── e2e/               # End-to-end browser validation and load harnesses
├── ui/                # UI integration tests (for example, canvas and OBS routes)
├── testcases/         # Evaluation test scenarios containing expectations.json and audio files
│   └── desert_basic/
│       ├── expectations.json
│       └── narration.wav
├── testdata/          # Preloaded test artifacts, sample images, and test sound files
│   ├── images/
│   └── playlists/
└── base.py             # Shared unittest fixture
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
python testing/e2e/validate_single_image_generation.py --testcase=testing/testcases/desert_basic --headless=True
```

### Available Command Flags
- `--testcase` (`-t`): Path to input audio narration file OR testcase folder containing `expectations.json` (required).
- `--output` (`-o`): Output MP4 video file path (default: `output/evaluation/eval_result1.mp4`).
- `--port` (`-p`): Server port (default: `0` for auto-allocated free port).
- `--headless`: Run browser headless (`True`/`False`).
- `--buffer` (`-b`): Buffer time in seconds after streaming completes (default: `15`).
- `--use_in_memory_artifacts`: Use `PreloadedInMemoryArtifactService` loaded with `testing/testdata` (default: `True`).
