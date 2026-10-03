# Live agent providers

`live_agent.provider` in `app.yaml` selects the backend. Gemini is the default
and currently the only registered implementation:

```yaml
live_agent:
  provider: gemini
  model_id: gemini-3.8-live
```

`LiveAgentProvider` owns model construction, streaming configuration, and the
event stream. `LiveAgentConfig` carries model selection, tool concurrency, and
optional context limits. Provider and model selection are app settings; theaters
can still set `live_agent.max_tool_workers`.

The session manager supplies the same provider instance to agent construction
and streaming. It continues to manage microphone input and explicit VAD activity
boundaries, tool budgets, reminders, callbacks, and browser events.

This boundary uses ADK's `BaseLlm`, `LiveRequestQueue`, `RunConfig`, and `Event`
contracts. To add a backend, implement an ADK model/connection adapter and a
`LiveAgentProvider`, then register its ID in `get_live_agent_provider`. Translate
audio, activity boundaries, content, tool calls, tool results, and turn completion
at that adapter boundary. Release connections when the stream closes or is
cancelled. The adapter can configure the output modalities its backend supports.

Gemini retains AUDIO output because Gemini 3.8 Live rejects TEXT. This abstraction
preserves current behavior and does not itself reduce API costs. An OpenAI Realtime
adapter with text output is a separate implementation.

The optional `uv run python -m scripts.live_agent_smoke` probe uses the configured
provider. It makes paid API requests; unit tests mock the transport.
