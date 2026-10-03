# Realtime voice cost audit

Investigated October 3, 2026. This is a repository and documentation audit, with illustrative estimates rather than a reconciliation of production invoices. Application behavior and customer pricing have not been changed.

## Finding

The current voice credit meter measures incoming PCM duration, while provider costs depend on model turns, retained multimodal context, and generated output. Repeated harness notifications can therefore create costs with no corresponding customer voice charge.

The application default in `app.yaml` is Gemini Developer API `gemini-3.8-live`. Per-theater settings and deployed environment variables may differ. The OpenAI backend is a separate implementation with text-only model output, so it is not an equivalent native speech-to-speech configuration.

Google documents that Gemini Live bills retained context again per turn, including raw audio history. Transcription does not replace that history with cheaper text. Compression reduces retained history and future charges. [Gemini billing behavior](https://ai.google.dev/gemini-api/docs/live-api/best-practices#pricing-and-billing).

## What the repository does

| Component | Observed behavior | Cost implication |
| --- | --- | --- |
| `services/live_stream_service.py`, `_send_audio_blob` | Records decoded audio bytes before forwarding to the provider queue. | Measures incoming media, not provider token usage or model output. |
| `services/live_agent_manager.py`, `record_audio_input`, `flush_usage_to_db` | Converts PCM bytes to minutes using 1,920,000 bytes/minute. | Correct duration conversion for mono, 16-bit, 16 kHz audio; does not measure history reprocessing. |
| `pricing/pricing_controller.py` | Defaults to 1 credit per input audio minute and 20 credits/USD. | Default voice revenue is $0.05 per incoming minute at the nominal exchange rate. Environment overrides are supported. |
| `services/live_agent_manager.py`, `_run_downstream` | Receives and broadcasts ADK events but does not feed usage metadata into the credit meter. | Provider usage is disconnected from customer voice billing. |
| `providers/gemini_live_agent_provider.py` | Native AUDIO output; input transcription enabled; manual activity boundaries; compression trigger 20,000 and target 8,000 from application config. | Audio output and retained context must enter the cost estimate. Input transcription introduces an additional charge. |
| `services/live_agent_manager.py`, `_run_live_tool_reminder_loop` | Sends a reminder every 3 seconds while the tool window is active. | Up to 20 reminder submissions per minute, subject to connection and queue state. |
| `services/priority_live_request_queue.py`, `record_model_tool_calls` | Only model-emitted function calls consume the tool budget. | Silent responses, ordinary responses, and reminders do not exhaust it. There is no reminder deadline or model-turn cap for Gemini. |
| `providers/live_agent_provider.py` | Gemini inherits `background_content_is_partial=False`, `requires_tool_reminders=True`. | Ordinary harness content is treated as complete input, unlike passive context in OpenAI. |
| Installed ADK, `gemini_llm_connection.py`, `_send_content` | Complete single-text notifications use realtime text input for Gemini 3.x; other complete content uses client content with `turn_complete=True`. Partial content uses `turn_complete=False`. | These are different wire paths; notifications are not guaranteed to map one-to-one to billed turns. |

Regular observability and repeated tool-definition injection are currently disabled in `app.yaml`. Agent-requested observability, collaboration updates, doodle snapshots, and tool reminders remain separate paths. The queue prioritizes messages and defers some notifications; it does not combine them into one payload or discard obsolete state snapshots.

## Architecture and billing boundary

The application keeps a persistent provider connection. Each audio chunk is streamed incrementally; it is not a fresh full-context request. The important boundary is generation of a new model response, including continuations triggered by tools or harness input. A persistent socket does not make retained history free.

For Gemini 3.8, client content without `turn_complete` waits for subsequent input. The model supports asynchronous tool-result scheduling (`SILENT`, `WHEN_IDLE`, `INTERRUPTED`) and does not support caching or the Batch API. Proactive audio is permanently enabled; measure its contribution and inspect what audio is actually streamed instead of assuming idle listening is free. [Gemini 3.8 model behavior](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-live).

OpenAI Realtime charges per created Response and automatically attempts prefix caching. Stable history can be much cheaper to reuse; modifications or truncation can reduce cache hits. [OpenAI realtime costs](https://developers.openai.com/api/docs/guides/voice-latency-cost).

The local OpenAI adapter already uses passive background content, disables periodic reminders, serializes response creation, and limits response turns. Its response usage is logged with modality/cache breakdowns, but the ADK conversion retains aggregate totals and drops those breakdowns. Its output is text-only: any separate speech synthesis requires separate cost accounting.

## Verified rates and estimates

Gemini 3.8 Live paid Developer API prices, USD per million tokens:

| Modality | Input | Output, including thinking |
| --- | ---: | ---: |
| Text | $0.75 | $4.50 |
| Audio | $3.00 | $12.00 |
| Image/video | $1.00 | — |

[Official Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing#gemini-3.8-live-gemini-3.8-live-extended-thinking-and-gemini-3.1-flash-live-preview).

At the default nominal conversion, provider break-even credits are:

```text
USD = sum over distinct billable generations (
    3.00 * audio_input_tokens
  + 0.75 * text_input_tokens
  + 1.00 * image_video_input_tokens
  + 12.00 * audio_output_tokens
  + 4.50 * text_output_tokens
) / 1,000,000

break_even_credits = USD * 20
```

Use provider-reported billable modality totals. Account for thoughts, transcription, and tool-use prompt counts according to their actual reporting semantics; do not blindly add overlapping aggregate and detailed fields.

An illustrative warm session has 12,000 retained audio input tokens and 3,000 text input tokens at each generation. Its context alone costs $0.03825, or 0.765 credits, per generation.

Assume 30 seconds of user audio and 30 seconds of generated model audio per wall-clock minute. Approximate generated audio at 25 tokens/second, giving 750 output audio tokens and $0.009 output cost per minute. For illustration, hold context size fixed; actual histories grow and periodically compress.

| Scenario | Estimated USD/wall-clock minute | Break-even credits/wall-clock minute |
| --- | ---: | ---: |
| Fresh audio only, no history/text overhead | $0.01125 | 0.225 |
| Warm context, 5 billed generations/minute | $0.20025 | 4.005 |
| Same context, 5 generations plus 20 reminder-triggered generations/minute | $0.96525 | 19.305 |

The fresh-audio row is a reference baseline, not a viable recurring price estimate. The reminder row assumes every submission becomes a separate billed generation; this has not been measured. All rows exclude additional transcription, thinking, images, other model/tool services, and infrastructure.

With 30 seconds of incoming audio, the current default meter charges only 0.5 credits per wall-clock minute. Thus the illustrative warm workload needs about 8.01 credits per incoming audio minute without reminders, or 38.61 if reminders cause all 20 extra generations. This illustrates why a universal incoming-audio-minute rate is unstable.

Break-even is not the selling price. The cheapest current credit package sells credits at $0.04 each, so recovering provider cost from that package requires 25 credits per provider dollar before payment fees or margin. A target gross margin `g` gives `selling_credits = provider_USD / (net_USD_per_credit * (1 - g))`.

For comparison, `gpt-realtime-2.1-mini` is $10/$0.30/$20 per million audio input/cached input/output tokens and $0.60/$0.06/$2.40 for text. These rates require the actual cache breakdown; applying Gemini's repeated-input model would misestimate OpenAI costs. [OpenAI model pricing](https://developers.openai.com/api/docs/models/gpt-realtime-2.1-mini).

## Recommended sequence

1. Capture provider usage in a separate internal cost ledger before changing customer deductions. Record provider/model, provider session, generation/turn identity, triggering event category, raw usage, modality totals, cache totals, and computed USD. Preserve Gemini's mapped details and OpenAI's raw response details.
2. Reconcile a bounded session against provider billing. Test an ordinary conversation, a user turn followed by silence with no tool calls, and tool/observability completions. Establish whether usage messages are final reports, interim snapshots, or incremental reports; deduplicate within a generation without subtracting legitimately repeated context across generations.
3. Remove periodic reminder polling. If recovery prompting is necessary, allow one bounded recovery attempt after a deadline. Budget generated responses, elapsed time, and estimated cost alongside function calls. Do not suppress results for already-issued tool calls.
4. Make routine state updates passive. Keep the latest pending canvas snapshot and send it alongside the next user turn or necessary tool continuation. Keep user commands and actionable completions explicit. Avoid duplicate full-canvas text/image updates and prefer concise deltas.
5. Coalesce independent completions within a short debounce window or send independent tool results together when available. Preserve dependent tool ordering and latency. This is application-level batching, not use of the provider Batch API. Keep microphone streaming responsive.
6. Experiment with smaller compression windows and shorter instructions/tool descriptions. Measure successful task completion and memory retention as well as cost; 8,000 is the current compression target, not a permanently fixed context size or hard ceiling.
7. Set customer pricing from measured cost distributions for the optimized harness. For exact cost alignment use provider usage; for predictable session-minute pricing, constrain model turns and retained context and choose a rate with a measured margin. Track incoming audio duration separately as a product metric.

## Verification and limits

Read the application/provider/queue/pricing implementation and installed ADK dispatch/usage conversion; checked current official provider documentation; independently calculated the example arithmetic in PowerShell. No paid API calls or production mutations were made. No tests were needed for this documentation-only audit. Production model overrides, real generation counts, modality mixes, usage-report granularity, and provider invoice totals remain unverified.
