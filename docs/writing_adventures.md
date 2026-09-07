# Writing Adventures for Narratron Buddy

Welcome to the Narratron Buddy Adventure Authoring Guide! This document provides everything you need to create, test, distribute, and submit your own interactive narrative adventures.

---

## 1. Overview & Architecture

A **Narratron Adventure** is a self-contained content package that turns Narratron Buddy into a tailored, multimodal storytelling experience. An adventure defines:

- **Narrative Logic & Agent Persona (`theater.yaml`)**: Custom instructions, story planning rules, persistent "sticky note" tracking, art direction, and pacing controls for Google Gemini.
- **Package Metadata (`metadata.json`)**: Title, description, genre tags, author credits, difficulty, and player count.
- **Lore Context (`lore/`)**: Deep backstory, world lore, factions, secrets, and NPC motivations provided directly to the language model.
- **Visual References (`references/`)**: Character portraits, maps, item designs, and cover artwork used to guide generative image tools.
- **Atmospheric Audio (`playlists/`)**: Loopable background music, ambient soundscapes, and thematic tracks organized into switchable playlists.

---

## 2. Distribution & Getting Featured

### Independent Distribution (Freely Distribute!)
All adventures are standard folder packages. **You are free to package and distribute your adventures however you like!**
- Zip your adventure folder and share it directly with friends or on Discord.
- Publish it on GitHub, itch.io, or gaming forums.
- Anyone running Narratron Buddy can drop your folder into their `adventures/` directory and immediately start playing.

### Getting Featured on narratron.app
If you would like your adventure to be hosted in the official cloud library and **featured directly on [narratron.app](https://narratron.app)** for all web users:
- Verify your adventure using the [Testing Guide](#5-testing-your-adventure) and [Pre-Submission Checklist](#6-submission-process-to-narratronapp).
- Ping **`syclonex`** on **Discord** with a link to your repository or your zipped package to request inclusion!

---

## 3. Getting Started: Developer Setup

To build an adventure with the recommended workflow:

### Step 1: Download an IDE
We recommend using a modern code editor such as:
- [Visual Studio Code](https://code.visualstudio.com/)
- [Google Antigravity](https://github.com/google/antigravity)
- [Cursor](https://www.cursor.com/)

### Step 2: Clone the Git Repository
Open your terminal and clone the `narratron-buddy` repository:
```powershell
git clone https://github.com/siddtheshah/narratron-buddy.git
cd narratron-buddy
```

### Step 3: Install `uv` and Dependencies
This project uses [`uv`](https://docs.astral.sh/uv/) for fast Python environment and package management:
```powershell
# Create a virtual environment and install project dependencies
uv venv
. .venv\Scripts\Activate.ps1
uv pip install -r requirements.txt
```

### Step 4: Configure Your Gemini API Key
To test story planning and dialogue generation locally:
1. Copy `.env-template` to `.env`:
   ```powershell
   Copy-Item .env-template .env
   ```
2. Open `.env` and fill in your Gemini API key:
   ```env
   GEMINI_API_KEY="your_actual_gemini_api_key_here"
   ```
*(You can obtain a free API key from [Google AI Studio](https://aistudio.google.com/)).*

---

## 4. Adventure Package Structure

All adventures live in subdirectories under `adventures/`. Take a look at `adventures/example_adventure/` in this repository as an official working reference template.

A complete adventure package looks like this:

```text
adventures/my-custom-adventure/
├── metadata.json           # Package metadata & UI display attributes
├── theater.yaml            # Story planning rules, agent persona & tool config
├── README.md               # (Optional) Notes for players/developers
├── lore/                   # Lore text files read by the agent
│   ├── readfirst_overview.txt # High-level guide always persisted in story context
│   ├── factions/
│   │   └── rebels.txt
│   └── locations/
│       └── citadel.txt
├── references/             # Character art, location images, and cover art
│   ├── cover.png
│   └── protagonist.png
└── playlists/              # Thematic audio folders with sound files
    ├── ambient/
    │   └── wind_whispers.mp3
    └── combat/
        └── intense_percussion.mp3
```

### 4.1. `metadata.json` Specification

`metadata.json` provides catalog details for the adventure browser:

```json
{
  "id": "my-custom-adventure",
  "title": "Secrets of the Obsidian Spire",
  "description": "Infiltrate a forsaken crystal spire and decipher its ancient astronomical mechanisms.",
  "author": "YourName",
  "genre": "Sci-Fi / Fantasy",
  "tags": ["Mystery", "Sci-Fi", "Exploration", "Puzzles"],
  "created_at": "2026-09-07T12:00:00Z",
  "cover_image": "references/cover.png",
  "difficulty": "Medium",
  "recommended_players": "1-4"
}
```

- **`id`** *(string, required)*: Unique URL slug (`my-custom-adventure`). Use lowercase alphanumeric characters and hyphens.
- **`title`** *(string, required)*: Display title.
- **`description`** *(string, required)*: 1–3 sentence hook summarizing the adventure premise.
- **`author`** *(string, required)*: Creator name or handle.
- **`genre`** *(string, required)*: e.g., `"Dark Fantasy"`, `"Cyberpunk"`, `"Cozy Mystery"`.
- **`tags`** *(array of strings)*: Discoverability keywords.
- **`cover_image`** *(string)*: Relative path to the cover artwork within the adventure package (e.g. `"references/cover.png"`).
- **`difficulty`** *(string)*: `"Easy"`, `"Medium"`, or `"Hard"`.
- **`recommended_players`** *(string)*: e.g. `"1"`, `"1-4"`, `"2-6"`.

---

### 4.2. `theater.yaml` Specification

Adventures use Narratron's standard theater configuration schema to define agent persona, art direction, audio pacing, and state preservation.

> [!NOTE]
> **Canonical Configuration Reference**:
> Rather than maintaining a separate explanation here, please refer to the **[theater.yaml Reference](/docs/theater-yaml)** for comprehensive documentation on all available sections and options—including `agent`, `visuals`, `image_generation`, `animation`, `interactive_canvas`, `music`, `story_planning`, and `chat`.

Adventures specifically rely on the **`story_planning`** section to govern the interactive adventure loop. Here is an adventure-focused configuration example:

```yaml
# Starting visual displayed on the canvas when the adventure starts
starting_image: "references/cover.png"

# Pacing and style direction for generative canvas visuals
visuals:
    cycle_length: 6
    style: "dark fantasy matte painting, glowing runes, cinematic lighting, moody atmospheric fog, high detail"

image_generation:
    enabled: true
    cooldown_duration: 6

music:
    playlists_folder: "playlists"
    style: "orchestral fantasy ambiance, haunting cello melodies, distant percussion, loopable background"

# Adventure Mode & Persistent State
story_planning:
    adventure_mode: true             # Required: Enables player action resolution & turn tracking
    auto_begin: true                 # Automatically initiates the opening narrative scene
    nodes_ahead: 3                   # Number of prospective plot beats the planner anticipates
    style: "engaging mystery with player agency, dramatic tension, and fair consequences"

    # Maximum number of persistent state stickies kept on the canvas board
    max_named_elements: 6

    # Initial sticky notes pinned at the beginning of the adventure
    initial_elements:
        "Player Character": "Name: Unnamed Explorer | Objective: Reach the Spire's apex | Inventory: Crystal lodestone | Condition: Healthy"
        "Spire Security Level": "Alert: Green (Unnoticed) | Defense automatons dormant"
        "Known Clues": "The Spire activates only when three harmonic keys are aligned."

    # Sticky note topics that the planner must NEVER discard during memory consolidation
    required_stickies:
        - "Player Character"
        - "Spire Security Level"

chat:
    cooldown_duration: 20
```

For full details on every field, default values, and advanced features (such as `character_voicing`, `require_voice_input`, interactive canvas surfaces, and video animation techniques), see the **[canonical theater.yaml reference](/docs/theater-yaml)**.

---

### 4.3. Authoring Lore (`lore/`)

Files in `lore/` are indexed and made available to the Gemini story planner to ground storytelling in your world's backstory, characters, and rules.

#### Persistent Story Guide: `readfirst_<document>.txt`

Any file starting with `readfirst_` (or `read_`, such as `lore/readfirst_overview.txt` or `lore/readfirst.txt`) is **always preloaded and permanently persisted in the active story context** across every turn of the adventure! (In contrast, other lore files are presented as an index and fetched dynamically on demand).

Because it is always persisted in story context, **it is best practice to use `readfirst_<document>.txt` as a high-level guide to quickly navigate and perform the adventure**. Think of this document as your Dungeon Master's Screen:
- **Story Structure & Narrative Timeline**: Outline the narrative arc into milestones, acts, or in-game days (e.g., *Day 1: Arrival & Introductions*; *Day 2: Sabotage & Clue Gathering*; *Day 3: Escalation & Climax*).
- **Directory & Asset Roadmap**: Provide a clear map of what content lives in each `lore/` subfolder (e.g., `characters/`, `locations/`, `factions/`).
- **Victory & Resolution Conditions**: Clearly state the win, loss, and escape conditions so the DM agent can steer toward satisfying conclusions.
- **Themes & DM Guidelines**: Set pacing cues, tone instructions, and boundaries on how quickly to reveal secrets.

#### Best Practices for Authoring Lore

1. **Annotate Visual Reference Paths in Lore**:
   When writing lore for characters, locations, artifacts, or factions that have corresponding visual assets in `references/`, annotate the reference path directly in the lore document itself:
   ```text
   # Character Info: Keeper Orun
   Orun is a seven-foot-tall brass automaton with an etched porcelain face mask and glowing amber optic lenses.
   Speaks with a rhythmic, deliberate cadence, often punctuated by a soft clicking in his chest.
   
   image_reference: references/keeper_orun.png
   ```
   *(Or simply `image_reference: keeper_orun.png`)*

   **Why this is a best practice**: When the story planner reads the lore file during play, having the reference path annotated directly in the lore allows the agent to immediately know the exact asset name to call with `show_image` or anchor visual prompts without guesswork or hallucinating file paths.

2. **Organize by Domain**: Split detailed worldbuilding into clear subdirectories:
   - `lore/readfirst_overview.txt`: High-level guide, timeline, and DM roadmap (persisted).
   - `lore/characters/*.txt`: Key NPCs, personalities, secrets, dialogue habits, and their annotated `image_reference`.
   - `lore/locations/*.txt`: Sensory descriptions, secrets, hazard triggers, and interactable elements.
   - `lore/factions/*.txt`: Groups, motives, rivalries, and allegiances.

3. **Keep Text Punchy & Structured**:
   Use bullet points, clear headings, and concise summaries. Dense blocks of prose dilute prompt attention and consume unnecessary context.

4. **Separate Public Knowledge from Secrets**:
   Clearly distinguish between common world knowledge and DM-only secrets:
   ```text
   # Public Knowledge
   Lord Vane is known as a benevolent benefactor to the town.

   # Secret Lore (DM Only)
   Lord Vane is covertly siphoning the town's life essence to power an obsidian golem beneath his estate.
   ```

---

### 4.4. Visual References (`references/`)

The `references/` folder contains images that represent characters, environments, maps, or artifacts.
- Images can be in `.png`, `.jpg`, `.jpeg`, or `.webp` format.
- **Cover Image**: Every adventure should have a cover image (e.g. `references/cover.png`). Reference this filename in `metadata.json` and `starting_image` in `theater.yaml`.
- During play, the agent can call `list_references` or `show_image` to present these pre-made visual assets to players. Annotating `image_reference: references/<filename>` in your lore documents ensures the agent automatically and reliably binds visual assets to specific characters and scenes.

---

### 4.5. Atmospheric Playlists (`playlists/`)

The `playlists/` folder contains subfolders representing different musical moods or scenes:
```text
playlists/
├── exploration/
│   ├── ancient_corridors.mp3
│   └── forgotten_ruins.mp3
├── suspense/
│   └── creeping_shadows.mp3
└── climax/
    └── battle_for_the_core.mp3
```
- Supported formats: `.mp3`, `.wav`, `.ogg`, `.flac`.
- Keep files compressed and loopable when possible.

---

## 5. Testing Your Adventure

Narratron Buddy provides multiple testing tiers so you can iterate quickly without needing complex cloud deployments.

### 5.1. Fast CLI Testing with `adventure_runner.py`

The quickest way to test narrative flow, state updates, and tool staging is the **Testlab Adventure Runner CLI**.

Make sure your `.env` has `GEMINI_API_KEY` set, then run:

#### Interactive CLI REPL
```powershell
uv run python testlab/adventure_runner.py --adventure my-custom-adventure
```
You can type player actions at the `Player > ` prompt and inspect:
- The story planner's scene reaction and narrative response.
- Active sticky notes updating in real time.
- Staged peripheral tool calls (music changes, canvas prompts, image triggers).
- Type `reset` to restart or `quit` to exit.

#### Automated Smoke Test
To verify that the adventure initializes cleanly and resolves an opening turn:
```powershell
uv run python testlab/adventure_runner.py --adventure my-custom-adventure --smoke
```
This executes a single turn, checks that story beats generate properly, and exits with code `0` on success.

---

### 5.2. Visual Browser Testing with Test Lab

You can test your adventure's UI and peripherals in a lightweight browser diagnostic without launching the full multi-user server:

```powershell
uv run python -m uvicorn testlab.server:app --host 127.0.0.1 --port 8015
```
Open **`http://127.0.0.1:8015/adventure-runner`** in your browser to interact with the visual test harness.

### 5.3. Final Testing Step: Upload & Deploy via `/deploy`

As the final, definitive step of testing before public distribution or submitting to narratron.app, upload your package folder directly via **[/deploy](https://narratron.app/deploy)** to test it in the full Narratron application without needing to configure backend API keys locally:

1. Navigate to **[narratron.app/deploy](https://narratron.app/deploy)**.
2. In the theater creation dashboard, locate the **"Drop Asset Folder or .ZIP here"** dropzone.
3. Click **Select** or drag-and-drop your custom adventure package folder (or compressed `.zip` archive). The system will mount your package, validate `theater.yaml`, and bundle `lore/`, `references/`, and `playlists/`.
4. Click **🚀 Deploy Theater** to launch the live theater instance.
5. Join the deployed room as host and test:
   - Verify that your `starting_image` displays immediately on the canvas.
   - Speak into your microphone or submit actions via chat to ensure turns resolve, sticky notes update, and narration flows smoothly.
   - Check that visual reference images, generative art, and atmospheric music playlists trigger properly in live play.

---

## 6. Submission Process (to narratron.app)

When your adventure is ready to be featured for everyone on **narratron.app**:

### Pre-Submission Checklist
- [ ] `metadata.json` exists, has a valid `id`, `title`, `author`, `genre`, and references a valid `cover_image`.
- [ ] `theater.yaml` is valid YAML and includes `story_planning` with `adventure_mode: true`.
- [ ] `required_stickies` match keys defined in `initial_elements`.
- [ ] `lore/` contains a `readfirst_<document>.txt` high-level guide with campaign roadmap and annotated visual reference paths.
- [ ] The adventure passes the smoke test:
  ```powershell
  uv run python testlab/adventure_runner.py --adventure <your-adventure-folder> --smoke
  ```
- [ ] Final verification: Uploaded the folder via [/deploy](https://narratron.app/deploy) and successfully complete an interactive play session in the full Narratron app.

### How to Submit
1. Push your adventure to a public Git repository (or prepare a `.zip` archive of your adventure folder).
2. Ping **`syclonex`** on **Discord**.
3. Include:
   - Adventure Title & Slug ID
   - Brief 1-sentence premise
   - Link to repository or download package
4. Your adventure will be reviewed, tested, and added to the official Google Cloud Storage repository for narratron.app!
