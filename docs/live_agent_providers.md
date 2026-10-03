# Live agent providers

`live_agent.provider` in `app.yaml` selects the backend. The application now uses
OpenAI Realtime 2.1 Mini. Set `OPENAI_API_KEY` in the server environment (or `.env`)
and restart the application before summoning the agent:

```yaml
live_agent:
  provider: openai
  model_id: gpt-realtime-2.1-mini
  openai:
    max_output_tokens: 1024
    max_response_turns: 6
    post_instructions_token_limit: 8000
    retention_ratio: 0.8
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

OpenAI streams native audio input while the browser's VAD marks speech active.
The adapter resamples Narratron's 16 kHz PCM to 24 kHz, commits at speech end,
and explicitly requests text output. Empty activities and audio outside activity
boundaries do not request a response. Activities shorter than 100 ms are discarded
to satisfy the API's minimum audio-buffer length. Server VAD is disabled.

Text input and summon greetings start a response. Background canvas/state messages
provide context without starting a response; OpenAI does not run the periodic
tool-reminder loop. Completed function calls are handed to ADK and results are
returned with their original call IDs. Parallel calls wait for every result before
requesting the next response. Tool results bypass the notification window so an
exhausted tool budget cannot strand an already-issued call. Responses never
overlap, and continuations are
limited by `max_response_turns` until fresh user input. This limit includes the
initial response. Tools already issued still execute and their results are sent
even when the continuation limit is reached.

OpenAI truncation retains a fraction of conversation history at the configured
token limit; it does not summarize. Gemini's `compaction` settings do not apply
to OpenAI. Per-response token usage, including audio and cached input tokens, is
logged and basic usage metadata is forwarded through ADK events.

Optional `openai.input_transcription_model` enables a separate input transcription
service for transcript events. It is off by default to avoid additional cost;
the Realtime model consumes the audio directly either way.

Connections and clients close on cancellation and protocol failure. OpenAI
connections do not use Gemini resumption handles or transparent reconnects;
after a failed connection, summon a fresh session. No vendor failover happens
automatically. Missing credentials fail explicitly when connecting.

To switch back, set `provider: gemini` and `model_id: gemini-3.8-live`. Gemini
retains AUDIO output because Gemini 3.8 Live rejects TEXT, along with its existing
VAD, reminder, and context-compression behavior. Other OpenAI Realtime model IDs
can be selected through `model_id` without changing the adapter.

The optional `uv run python -m scripts.live_agent_smoke` probe uses the configured
provider. It makes paid API requests; unit tests mock the transport.

Protocol reference: [OpenAI Realtime conversations](https://developers.openai.com/api/docs/guides/realtime-conversations)
and [context truncation](https://developers.openai.com/api/docs/guides/voice-latency-cost).
