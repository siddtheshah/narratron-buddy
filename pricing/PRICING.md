# Pricing Model & Credit Consumption Rates

## Quick Reference Summary Rates

* **Exchange Rate:** $1.00 USD = 20 Credits ($0.05 / Credit)
* **Live-agent Tool Calls:** 0.15 Credits / call ($0.0075 / call), including lookups and failed calls
* **Images Generated:** 1.0 Credit / image ($0.05 / image)
* **Music Generated:** 2.0 Credits / track ($0.10 / track)
* **Animations:** 6.5 Credits / successful animation ($0.325 / animation)
* **Adventure Mode / Story Planning:** 0.5 Credits / action ($0.025 / action, or ~2.5 Credits / min at 5 calls/min)
* **Storage Used:** 1.0 Credit / GB / month ($0.05 / GB / month, or ~0.033 Credits / GB / day)

---

## 1. Credit Conversion Base

* **Exchange Rate:** **$1.00 USD = 20 Credits**
* **Credit Value:** **1 Credit = $0.05 USD**

---

## 2. API Cost Lookup & Proposed Credit Rates

| Cost Driver | Underlying API / Provider | Estimated Raw API Cost | Billed USD Rate (with ~2x–3x margin) | Proposed Credit Consumption Rate |
| :--- | :--- | :--- | :--- | :--- |
| **1) Live-agent Tool Calls** | **Configured live-agent model** | Varies with context, audio, output, and caching | $0.0075 / call | **0.15 Credits per model-emitted tool call** |
| **2) Images Generated** | **Gemini Image / Imagen 3 Fast** (`gemini-3.1-flash-lite-image`) | ~$0.020 – $0.030 / image | $0.05 / image | **1.0 Credit per image** <br>*(0.5 Cr for standard draft, 1.0 Cr for HD/ref)* |
| **3) Music Generated** | **Lyria Music API** (`gemini-2.5-flash-lyria`) | ~$0.030 – $0.050 / track | $0.10 / track | **2.0 Credits per music track** |
| **4) Animations** | **Animation tool providers** | Varies by technique | $0.325 / animation | **6.5 Credits per successful animation** |
| **5) Adventure Mode / Story Planning** | **Gemini 3.7 Flash API** (`gemini-3.7-flash`) <br>*(4k tokens/call, ~5 calls/min = 20k tokens/min)* | ~$0.0006 – $0.0012 / action <br>*(~$0.003 – $0.006 / min)* | $0.025 / action <br>*(~$0.125 / min at 5 calls/min)* | **0.5 Credits per action / plan turn** <br>*(~2.5 Cr / min at 5 calls/min)* |
| **6) Storage Used** | **Google Cloud Storage (GCS) / S3** *(Persistent assets, audio, snapshots)* | ~$0.023 / GB / month <br>*(~$0.00077 / GB / day)* | $0.05 / GB / month | **1.0 Credit per GB per month** <br>*(or ~0.033 Credits / GB / day)* |

---

## 3. Detailed Breakdown by Cost Driver

### 1. Live-agent Tool Calls

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
default is `0.15` credits; calibrate it against observed provider costs
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

### 2. Images Generated
* **Raw API Pricing:**
  * `gemini-3.1-flash-lite-image` / Imagen 3 Fast: **~$0.020 – $0.030 per image**.
* **Proposed Rates:**
  * **Standard Image Generation:** **1 Credit / image** ($0.05 / image).
  * **Draft / Low-Res Preview:** **0.5 Credits / image** ($0.025 / image).
  * **Margin:** ~1.6x – 2.5x margin. Simple 1:1 credit calculation is intuitive for theater owners.

### 3. Music Generated
* **Raw API Pricing:**
  * `gemini-2.5-flash-lyria`: **~$0.030 – $0.050 per track**.
* **Proposed Rates:**
  * **Standard Music Generation:** **2 Credits / track** ($0.10 / track).
  * **Margin:** ~2x – 3x margin.

### 4. Adventure Mode / Story Planning (Gemini 3.7 Flash API)
* **Usage Pattern & Parameters:**
  * Model: `gemini-3.7-flash`
  * Token Consumption: ~4,000 tokens per user action call (~4k tokens/call).
  * Call Frequency: ~5 calls per minute (300 calls per hour, or ~20,000 tokens per minute).
* **Raw API Pricing:**
  * Gemini 3.7 Flash: ~$0.15 / 1M input tokens, ~$0.60 / 1M output tokens (blended ~$0.15 – $0.30 per 1M tokens).
  * **Raw Cost per Action:** ~4,000 tokens × ~$0.00000025/token ≈ **~$0.0010 USD / action** (~$0.005 / minute at 5 calls/min).
* **Proposed Rates:**
  * **Standard Adventure Action:** **0.5 Credits per action / plan turn** ($0.025 USD / action).
  * **Per-Minute Rate (at 5 calls/min):** **2.5 Credits / min** ($0.125 / min).
  * **Margin:** Covers prompt context expansion, tool calls, and high-tier flash reasoning overhead.

### 5. Storage Used (Persistent Theater Storage)
* **Raw Cloud Storage Pricing:**
  * GCS Standard Storage: **~$0.023 per GB / month** ($0.00077 per GB / day).
* **Proposed Rates:**
  * **Monthly GB Billing:** **1 Credit per GB per month** ($0.05 / GB / month).
  * **Daily Fee (for DB Daemon / Cleanup Billing):** **0.033 Credits per GB / day**.
  * **Flat Base Storage for Persistent Theaters (< 500 MB):** **0.1 Credits / day** (~3 Credits / month = $0.15 / month).
  * **Margin:** ~2x margin over cloud storage rates.

---

## 4. Example Theater Session Breakdown

If a Theater Owner runs a **30-minute interactive live session** with 5 participating audience members in Adventure Mode:

* **Live-agent Tool Calls:** Assuming 4 calls per player action, 150 actions × 4 calls × 0.15 Cr/call = **90 Credits** ($4.50). Incoming audio is not charged separately.
* **Images:** 30 dynamic scene images generated × 1 Cr/img = **30 Credits** ($1.50)
* **Music:** 8 dynamic background music tracks generated × 2 Cr/track = **16 Credits** ($0.80)
* **Adventure Mode Actions:** 150 action calls (30 mins @ 5 calls/min × 4k tokens) × 0.5 Cr/action = **75 Credits** ($3.75)
* **Storage:** 500 MB theater assets saved for 1 month = **0.5 Credits** ($0.025)
* **Total Session Cost to Theater Owner:** **211.5 Credits** (~$10.58 USD total)

