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
│   ├── overview.txt
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

`theater.yaml` defines how the Gemini storytelling agent behaves, what tools it can call, and how state is preserved across turns.

Here is an example with detailed annotations:

```yaml
# ==============================================================================
# Agent Persona & Pacing
# ==============================================================================
agent:
    proactivity: false               # Keep false to let user lead interactions
    affective_dialog: false          # Keep false for consistent DM narration
    special_instructions: "Act as an evocative, impartial dungeon master. React dynamically to player actions, maintain mystery, and present consequences for failure."

# Starting visual displayed when the canvas loads
starting_image: "cover.png"

# ==============================================================================
# Visuals & Generative Art Direction
# ==============================================================================
visuals:
    cycle_length: 6                  # Beat frequency for proposing visual shifts
    style: "dark fantasy matte painting, glowing runes, cinematic lighting, artstation trending, moody atmospheric volumetric fog, high detail"

image_generation:
    enabled: true                    # Enable AI image generation on the canvas
    cooldown_duration: 6             # Cooldown in seconds between image generations

# ==============================================================================
# Dynamic Music & Playlists
# ==============================================================================
music:
    use_generated_music: true        # Enable music generation/selection tools
    playlists_folder: "playlists"    # Relative path to playlists directory
    generation_cooldown: 90          # Seconds between music generation requests
    switch_cooldown: 15              # Seconds between track switching
    style: "orchestral fantasy ambiance, haunting cello melodies, distant percussion, loopable background"

# ==============================================================================
# Story Planning & Sticky Note State Management
# ==============================================================================
story_planning:
    adventure_mode: true             # Must be true to enable full narrative tracking
    auto_begin: true                 # Automatically initiate the opening scene
    character_voicing: false         # Toggle agent voicing of specific NPCs
    text_beautification: true        # Format output cleanly with markdown
    nodes_ahead: 3                   # Number of plot beats the planner anticipates
    style: "engaging mystery with emergent player choices, dramatic tension, and clue discovery"
    cooldown_duration: 8             # Minimum cooldown between background consolidations
    require_user_input: true         # Pause progression until user submits an action
    action_cooldown_words_per_second: 20
    action_cooldown_max_seconds: 25

    # Maximum number of persistent state stickies kept on the canvas board
    max_named_elements: 6

    # Initial sticky notes pinned at the beginning of the adventure
    initial_elements:
        "Player Character": "Name: Unnamed Explorer | Objective: Reach the Spire's apex | Inventory: Crystal lodestone, grappling hook | Condition: Healthy"
        "Spire Security Level": "Alert: Green (Unnoticed) | Defense automatons dormant | Barriers active on level 3"
        "Known Lore & Clues": "The Spire activates only when three harmonic keys are aligned."

    # Stickies that the planner must NEVER discard during memory consolidation
    required_stickies:
        - "Player Character"
        - "Spire Security Level"

# Chat cooldown in seconds
chat:
    cooldown_duration: 20
```

---

### 4.3. Authoring Lore (`lore/`)

Files in `lore/` are indexed and injected into the Gemini story planner's context window. 

**Best Practices for Writing Lore:**
1. **Organize by Domain**: Split lore into subdirectories or discrete files:
   - `lore/overview.txt`: The premise, global rules, and win/loss conditions.
   - `lore/locations/*.txt`: Sensory descriptions, secrets, and interactable elements of specific zones.
   - `lore/factions/*.txt`: Groups, their goals, relationships, and rivalries.
   - `lore/characters/*.txt`: Key NPCs, personalities, secrets, dialogue habits, and desires.
2. **Keep Text Punchy**: Use bullet points, clear headings, and concise summaries. Large walls of unstructured text dilute prompt context.
3. **Separate Public Knowledge from Secrets**:
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
- During play, the agent can call `list_references` or `show_image` to present these pre-made visual assets to players.

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

---

### 5.3. Full App Testing (Local Mode)

To experience the adventure exactly as a player would with live speech, canvas doodling, and real-time audio:

```powershell
uv run main.py --testing_use_local
```
- Open `http://localhost:8000` in your browser.
- Select your adventure from the adventure selection drawer.
- Play through opening turns and test multimodal interactions!

---

## 6. Submission Process (to narratron.app)

When your adventure is ready to be featured for everyone on **narratron.app**:

### Pre-Submission Checklist
- [ ] `metadata.json` exists, has a valid `id`, `title`, `author`, `genre`, and references a valid `cover_image`.
- [ ] `theater.yaml` is valid YAML and includes `story_planning` with `adventure_mode: true`.
- [ ] `required_stickies` match keys defined in `initial_elements`.
- [ ] `lore/` contains clear background context.
- [ ] The adventure passes the smoke test:
  ```powershell
  uv run python testlab/adventure_runner.py --adventure <your-adventure-folder> --smoke
  ```

### How to Submit
1. Push your adventure to a public Git repository (or prepare a `.zip` archive of your adventure folder).
2. Ping **`syclonex`** on **Discord**.
3. Include:
   - Adventure Title & Slug ID
   - Brief 1-sentence premise
   - Link to repository or download package
4. Your adventure will be reviewed, tested, and added to the official Google Cloud Storage repository for narratron.app!
