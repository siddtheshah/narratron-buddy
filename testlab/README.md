# Narratron Test Lab

Run the two browser diagnostics without starting the full Narratron app:

```powershell
python -m uvicorn testlab.server:app --host 127.0.0.1 --port 8015
```

Open `http://127.0.0.1:8015/` for the Test Lab index. It links to microphone/VAD testing, image effects, image/music/text provider benchmarks, and the asynchronous story-planner bridge.

The Music Provider Bench can run a regular generator or the **TEST ONLY: Generate base + audio adapter** fixture. Select Lyria as the base and Stable Audio 3 Small Music Base A2A (FAL) as the adapter, then use the source-preservation control (lower noise preserves more of the Lyria track). This fixture intentionally generates a new base for an A/B evaluation; production variants should instead pass a previously generated or stored track to `MusicAdapter`. Set `FAL_KEY` or `FAL_API_KEY` alongside the Lyria/Gemini key before launching it.

To bypass the browser and exercise one real Vertex-backed planner turn, run:

```powershell
python testlab/story_planner_smoke.py
```

