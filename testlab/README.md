# Narratron Test Lab

Run the two browser diagnostics without starting the full Narratron app:

```powershell
python -m uvicorn testlab.server:app --host 127.0.0.1 --port 8015
```

Open `http://127.0.0.1:8015/vad` for microphone/VAD testing or `/effects` for the image-effects lab.
