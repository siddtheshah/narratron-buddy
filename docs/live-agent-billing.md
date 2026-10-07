# Live-agent billing

The live agent charges once for every model-emitted tool call, including internal
lookups, playback controls, status messages, and calls that later fail. Each call
in a batch counts separately. Tool results and backend-generated callbacks do
not count as additional live-agent calls.

Incoming microphone audio remains available in session diagnostics but incurs
no live-agent audio-input charge. Text and image input also incur no input charge.
The call rate is intended to amortize the live model's input and output costs.
Existing image, music, story-planning, voicing, canvas, and animation charges apply
in addition when their existing billing callbacks fire.

Set `LIVE_AGENT_TOOL_CALL_CREDIT_RATE` (or
`PRICING_LIVE_AGENT_TOOL_CALL_CREDIT_RATE`) to configure credits per call. The
initial default is `0.01` credits; calibrate it against observed provider costs
and tool-call volume. Setting it to `0` disables the base call charge.

`/api/pricing` exposes `live_agent_tool_call_credit_rate` and accepts
`live_agent_tool_calls` for estimates. Session usage exposes
`live_agent_tool_calls`; account usage exposes `total_live_agent_tool_calls`.
The durable usage ledger stores call counts and uses the existing idempotency
keys to prevent duplicate debits when a database write is retried.

Before deploying to an existing Cloud SQL database, apply the additive schema
migrations with `uv run python scripts/migrate_cloud_sql_schema.py` as the owning
database role. The new columns are included in fresh database initialization.
This code change does not apply migrations to a live database or deploy the app.
