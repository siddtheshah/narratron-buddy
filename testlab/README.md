# Narratron Test Lab

Run the two browser diagnostics without starting the full Narratron app:

```powershell
python -m uvicorn testlab.server:app --host 127.0.0.1 --port 8015
```

Open `http://127.0.0.1:8015/` for the Test Lab index. It links to microphone/VAD testing, image effects, image/music/text provider benchmarks, and the asynchronous story-planner bridge.

To bypass the browser and exercise one real Vertex-backed planner turn, run:

```powershell
python testlab/story_planner_smoke.py
```

