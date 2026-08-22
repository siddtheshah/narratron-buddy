# Narratron Test Lab

Run the two browser diagnostics without starting the full Narratron app:

```powershell
python -m uvicorn testlab.server:app --host 127.0.0.1 --port 8015
```

Open `http://127.0.0.1:8015/` for the Test Lab index. It links to microphone/VAD testing, image effects, image/music/text provider benchmarks, and the asynchronous story-planner bridge.

The Music Provider Bench can run a regular generator or the **TEST ONLY: Generate base + audio adapter** fixture. Select Lyria as the base and Stable Audio 3 Small Music Base A2A (FAL) as the adapter, then use the source-preservation control (lower noise preserves more of the Lyria track). This fixture intentionally generates a new base for an A/B evaluation; production variants should instead pass a previously generated or stored track to `MusicAdapter`. Set `FAL_KEY` or `FAL_API_KEY` alongside the Lyria/Gemini key before launching it.

The Speech Provider Bench compares Gemini Flash TTS and ByteDance **Seed Speech v2** on FAL using fixed narrative dialogue lines. Gemini uses `GEMINI_API_KEY`; Seed Speech uses `FAL_KEY` or `FAL_API_KEY`. (Seedance is ByteDance's video family; Seed Speech is its FAL TTS endpoint.)

To bypass the browser and exercise one real Vertex-backed planner turn, run:

```powershell
python testlab/story_planner_smoke.py
```

To exercise one real A2UI Canvas generation turn in an isolated temporary theater,
using a two-knight health-bar scenario, run:

```powershell
python testlab/a2ui_canvas_smoke.py
```

Add `--image testlab/images/trace-knight-sword.png` to include a canvas image in the request.

The same configurable test is available in the Test Lab server at `/a2ui-canvas`. It accepts a model,
canvas request, optional workspace-local image, expected surface count, and an optional persistence
expectation. The browser default is a general canvas prompt; the health-bar scenario is reserved for the CLI smoke test.

