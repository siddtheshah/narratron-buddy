# Pricing Model & Credit Consumption Rates

## Quick Reference Summary Rates

* **Exchange Rate:** $1.00 USD = 20 Credits ($0.05 / Credit)
* **Voice Minutes:** 1.0 Credit / minute ($0.05 / min)
* **Images Generated:** 1.0 Credit / image ($0.05 / image)
* **Music Generated:** 2.0 Credits / track ($0.10 / track)
* **Adventure Mode / Story Planning:** 0.65 Credits / action ($0.0325 / action, or ~3.25 Credits / min at 5 calls/min)
* **Storage Used:** 1.0 Credit / GB / month ($0.05 / GB / month, or ~0.033 Credits / GB / day)

---

## 1. Credit Conversion Base

* **Exchange Rate:** **$1.00 USD = 20 Credits**
* **Credit Value:** **1 Credit = $0.05 USD**

---

## 2. API Cost Lookup & Proposed Credit Rates

| Cost Driver | Underlying API / Provider | Estimated Raw API Cost | Billed USD Rate (with ~2x–3x margin) | Proposed Credit Consumption Rate |
| :--- | :--- | :--- | :--- | :--- |
| **1) Voice Minutes** | **Gemini Live Multimodal API** *(WebSockets bidirectional audio input/output)* | ~$0.010 – $0.018 / min | $0.05 / min | **1.0 Credit per minute** <br>*(or 0.5 Cr/min for standard voice)* |
| **2) Images Generated** | **Gemini Image / Imagen 3 Fast** (`gemini-3.1-flash-lite-image`) | ~$0.020 – $0.030 / image | $0.05 / image | **1.0 Credit per image** <br>*(0.5 Cr for standard draft, 1.0 Cr for HD/ref)* |
| **3) Music Generated** | **Lyria Music API** (`gemini-2.5-flash-lyria`) | ~$0.030 – $0.050 / track | $0.10 / track | **2.0 Credits per music track** |
| **4) Adventure Mode / Story Planning** | **Gemini 3.7 Flash API** (`gemini-3.7-flash`) <br>*(4k tokens/call, ~5 calls/min = 20k tokens/min)* | ~$0.0006 – $0.0012 / action <br>*(~$0.003 – $0.006 / min)* | $0.0325 / action <br>*(~$0.1625 / min at 5 calls/min)* | **0.65 Credits per action / plan turn** <br>*(~3.25 Cr / min at 5 calls/min)* |
| **5) Storage Used** | **Google Cloud Storage (GCS) / S3** *(Persistent assets, audio, snapshots)* | ~$0.023 / GB / month <br>*(~$0.00077 / GB / day)* | $0.05 / GB / month | **1.0 Credit per GB per month** <br>*(or ~0.033 Credits / GB / day)* |

---

## 3. Detailed Breakdown by Cost Driver

### 1. Voice Minutes (Gemini Live API)
* **Raw API Pricing:**
  * Audio Input: ~$0.70 / 1M tokens (~1,500 audio tokens/min ≈ **$0.00105 / min**)
  * Audio Output (WebSockets audio stream): ~$4.00 / 1M tokens (~1,800 audio tokens/min ≈ **$0.00720 / min**)
  * Bandwidth & WebSockets streaming overhead: ~$0.003 / min
  * **Total Raw Cost:** **~$0.012 / minute**
* **Proposed Rates:**
  * **Standard Voice Stream (Live Preview):** **1 Credit / min** ($0.05 / min).
  * **Margin:** ~4x margin. This covers real-time WebSockets server hosting, audio encoding/decoding overhead, and network egress.

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
  * **Standard Adventure Action:** **0.65 Credits per action / plan turn** ($0.0325 USD / action).
  * **Per-Minute Rate (at 5 calls/min):** **3.25 Credits / min** ($0.1625 / min).
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

* **Voice:** 30 live minutes × 1 Cr/min = **30 Credits** ($1.50)
* **Images:** 30 dynamic scene images generated × 1 Cr/img = **30 Credits** ($1.50)
* **Music:** 8 dynamic background music tracks generated × 2 Cr/track = **16 Credits** ($0.80)
* **Adventure Mode Actions:** 150 action calls (30 mins @ 5 calls/min × 4k tokens) × 0.65 Cr/action = **97.5 Credits** ($4.875)
* **Storage:** 500 MB theater assets saved for 1 month = **0.5 Credits** ($0.025)
* **Total Session Cost to Theater Owner:** **142.0 Credits** (~$8.20 USD total)

