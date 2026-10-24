---
name: writing-adventures
description: Comprehensive authoring guide and procedural runbook for designing, structuring lore, configuring theater.yaml, testing, and distributing interactive adventures in Narratron Buddy.
---

# Narratron Buddy Adventure Authoring Guide

This skill provides comprehensive instructions, design patterns, architectural standards, and validation workflows for authoring interactive narrative adventures in Narratron Buddy.

---

## 1. Overview & Adventure Architecture

A **Narratron Adventure** is a self-contained content package stored under `adventures/<adventure-id>/`. It turns Narratron Buddy into a multimodal interactive storytelling experience powered by Google Gemini, generative visuals, and atmospheric audio.

Every adventure package contains six core components:

```text
adventures/<adventure-slug>/
├── metadata.json           # Catalog metadata, genre tags, and browser display attributes
├── theater.yaml            # Story planning rules, agent persona, and tool settings
├── planning.yaml           # Story planning schema, sticky definitions, fields, and initial values
├── game/                   # Optional declarative game; see docs/game_packages.md
│   └── game.yaml           # State, CEL rules, actions and view (game.yml/game.json also supported)
├── README.md               # (Optional) Creator notes or developer documentation
├── lore/                   # Worldbuilding, DM screen, character dossiers, locations, and rules
│   ├── RESPONDER.md         # Pinned immediate-response instructions
│   ├── PLANNER.md           # Pinned background-planning instructions
│   ├── RECORDER.md          # Pinned factual recording and game-action instructions
│   ├── characters/         # NPC dossiers with image references and behavior guidelines
│   ├── locations/          # Regional descriptions, local commodities, and hazards
│   └── factions/           # Agendas, rivalries, and allegiances
├── references/             # Cover art, portraits, scenes, and environment concepts
│   ├── characters/         # One folder per character
│   │   └── Keeper Orun/
│   │       ├── 1.png
│   │       └── character.yaml
│   ├── scenes/             # One folder per scene
│   │   └── The Desk of Origins/
│   │       ├── 1.png
│   │       └── scene.yaml
│   └── cover.png
└── playlists/              # Thematic audio folders with sound files
    ├── ambient/
    │   └── theme.mp3
    └── tension/
        └── suspense.mp3
```

---

## 2. Quickstart: The Adventure Authoring Workflow

For adventures with a board or structured game mechanics, prefer `game/game.yaml`
for human editing. Use comments and `|-` for multiline CEL expressions, and keep
CEL literal quotes in view props (`text: '"Play"'`). Keep exactly one declaration
(`game.yaml`, `game.yml`, or `game.json`) in `game/`. Follow `docs/game_packages.md`
for contracts and validation; the example adventure includes commented checkers.

When creating a new adventure, follow this sequence:

1. **Scaffold the Package**: Create `adventures/<adventure-slug>/` with `lore/`, `references/characters/`, `references/scenes/`, `references/`, and `playlists/` folders.
2. **Author `metadata.json`**: Configure the display title, slug ID, genre, difficulty, player count, and cover image.
3. **Configure `theater.yaml` & `planning.yaml`**: Set `adventure_mode: true` and define agent persona, art, and music direction in `theater.yaml`. Define structured sticky notes, initial values, and required stickies in `planning.yaml`.
4. **Write independent role guides**: Put opening, tone, action-resolution and dice rules in `lore/RESPONDER.md`; put campaign milestones, hidden consequences and planner sticky tracking in `lore/PLANNER.md`; put factual note reconciliation and game actions in `lore/RECORDER.md`. Each module has only its own complete guide pinned and cannot read the others. Put shared mechanics and facts in separate lore documents.
5. **Flesh Out Modular Lore**: Create character dossiers, location profiles, and faction files. Annotate characters with their canonical name (`image_reference: Keeper Orun`); annotate locations with their canonical scene name (`image_reference: The Desk of Origins`); other assets can use `image_reference: references/<filename>`.
6. **Add Assets**: Place character portraits in `references/characters/<Character Name>/1.png` (each character requires its own folder; loose images directly under `references/characters/` are unsupported), scene imagery in `references/scenes/<Scene Name>/1.png` (with `scene.yaml`; loose images directly under `references/scenes/` are unsupported), cover/location references in `references/`, and loopable tracks in `playlists/`. During play, character portraits are copied into `output/artifacts/updated_references/characters/`, scene imagery into `output/artifacts/updated_references/scenes/`, and all appearance updates stay there. The live agent uses `<Character Name>` in visual prompts to attach the latest portrait; authors can manually copy session updates back into `references/characters/` and `references/scenes/`. See `docs/writing_adventures.md` for session reset and reference behavior.
7. **Test Locally**: Run smoke and interactive tests using `testlab/adventure_runner.py`.
8. **Final Verification**: Upload the folder to [narratron.app/deploy](https://narratron.app/deploy) for live verification.

---

## 3. Package Manifest: `metadata.json`

The `metadata.json` file registers the adventure in the Narratron catalog and UI selectors:

```json
{
  "id": "obsidian-spire",
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

### Field Requirements
- **`id`** *(string, required)*: Lowercase kebab-case slug matching the folder name (e.g., `my-custom-adventure`, `obsidian-spire`).
- **`title`** *(string, required)*: Display title.
- **`description`** *(string, required)*: 1–3 sentence hook summarizing the adventure premise.
- **`author`** *(string, required)*: Author handle or name.
- **`genre`** *(string, required)*: Primary genre (e.g., `"Dark Fantasy"`, `"Arctic Thriller"`, `"Historical Trade"`).
- **`tags`** *(array of strings)*: Discoverability tags.
- **`cover_image`** *(string, required)*: Relative path to cover art (e.g., `"references/cover.png"`).
- **`difficulty`** *(string)*: `"Easy"`, `"Medium"`, or `"Hard"`.
- **`recommended_players`** *(string)*: e.g. `"1"`, `"1-4"`, `"2-6"`.

---

## 4. Theater & Planning Configuration

Adventures use `theater.yaml` to govern agent persona, art direction, and runtime flags, and `planning.yaml` to define sticky note contracts and deep planning schemas.

### 4.1. Standard Adventure Theater Template (`theater.yaml`)

```yaml
live_agent:
    special_instructions: "Carry user actions faithfully and let the story planner resolve questions. Guide the adventure with dramatic tension and clear consequences."

starting_image: "references/cover.png"

visuals:
    cycle_length: 6
    style: "dark fantasy matte painting, glowing runes, cinematic lighting, moody atmospheric fog, high detail"

image_generation:
    enabled: true
    cooldown_duration: 6

music:
    use_generated_music: true
    playlists_folder: "playlists"
    generation_cooldown: 90
    switch_cooldown: 15
    style: "orchestral fantasy ambiance, haunting cello melodies, distant percussion, loopable background"

story_planning:
    adventure_mode: true             # MANDATORY: Enables player action resolution & turn tracking
    auto_begin: true                 # Automatically initiates opening scene without waiting for prompt
    character_voicing: true          # Gives NPCs distinct dialogue voices
    text_beautification: true        # Polishes narrative output
    style: "engaging mystery with player agency, dramatic tension, and fair consequences"

    # Require sticky definitions, initial values, and visibility in planning.yaml.
    enforce_structured: true

    cooldown_duration: 10
    require_user_input: true

chat:
    cooldown_duration: 20

speech:
    voice_language_code: "en-US"
    accent_augmentation: true        # Amplifies character accent voice tags into explicit delivery instructions
```

The top-level `notepad` section is solely for normal narration and configures
`initial_stickies`, `max_stickies`, `required_stickies`, and `hidden_stickies`.
Adventure Mode ignores these settings; all adventure topics are required and
pinned in `planning.yaml` order, and capacity includes every declared topic.
With `story_planning.enforce_structured: true` (the default), a missing or empty
contract is a configuration error. Set this flag to `false` to let recorder and
planner also create and update shared freeform topics without a contract entry.
Declared topics retain ownership and field validation. Freeform topics are
visible by default and persist across saves and restarts; older optional notes
are evicted at capacity, with at least one freeform slot alongside required topics.

### 4.2. Deep Planning Schema Template (`planning.yaml`)

Adventures use `planning.yaml` to define the persistent state that is
handled by the deep story planner.

```yaml
# Deep Planner Schema: planning.yaml
# All fields and sticky values are strictly strings. No arrays, numbers, or complex JSON Schema objects.

"Player Character":
  owner: recorder
  description: "Established player identity, objective, and physical condition."
  render: "Name: {name} | Objective: {objective} | Inventory: {inventory} | Condition: {condition}"
  fields:
    name: "Character name"
    objective: "Current primary goal"
    inventory: "Key items carried"
    condition: "Physical and mental status"
  initial:
    name: "Unnamed Explorer"
    objective: "Reach the Spire's apex"
    inventory: "Crystal lodestone"
    condition: "Healthy"

"Spire Security Level":
  owner: recorder
  description: "Alert level and defense automaton activity."
  render: "Alert: {alert} | Automatons: {automatons}"
  fields:
    alert: "Current alert level"
    automatons: "Automaton patrol status"
  initial:
    alert: "Green (Unnoticed)"
    automatons: "Dormant"

"Known Clues":
  owner: recorder
  hidden: true
  description: "Discovered clues and historical hints."
  initial: "The Spire activates only when three harmonic keys are aligned."
```

### Critical Rules for `theater.yaml` & `planning.yaml`
1. **Use `live_agent` for live-agent settings**: Put persona flags and `special_instructions` under `live_agent`; the former `agent` and `agent_internal` section names are no longer used.
2. **`adventure_mode: true` is mandatory**: Without this flag, Narratron operates in passive storytelling mode rather than interactive adventure mode.
3. **Move stickies to `planning.yaml`**: Define initial notes, field schemas, and rendering strings in `planning.yaml`. All entries are assumed required and pinned in order.
4. **Sticky visibility**: Set `hidden: true` on each hidden topic in `planning.yaml`. The `notepad.hidden_stickies` list applies only to normal narration.
5. **`starting_image` must exist**: Ensure the file referenced by `starting_image` is present in your adventure folder (usually under `references/`).
6. **`speech` settings**: Configure `voice_language_code` and `accent_augmentation` to shape NPC voice generation; backend speech provider and model are application-level operational settings defined in `app.yaml`, which override corresponding fields.
7. **Planner-owned 'Upcoming Encounter Suggestion' sticky**: Include a hidden sticky owned by the planner (`owner: planner`, `hidden: true`) with fields for `encounter`, `story_goal`, and `drama` to continuously stage narrative obstacles and dramatic tension.


---

## 5. Lore Architecture & Design Patterns

The `lore/` folder is the core storytelling engine. Proven architectural patterns for structuring interactive adventure lore ensure high narrative consistency, deep player agency, and reliable tool calling.

### 5.1. Pinned role instructions: `RESPONDER.md`, `PLANNER.md`, and `RECORDER.md`

Every adventure requires all three files at the top level of `lore/`. The runtime pins the full `RESPONDER.md` only for the responder, the full `PLANNER.md` only for the planner, and the full `RECORDER.md` only for the recorder. None of the modules can read or search the others' guides. Other `.txt` and `.md` lore is indexed and fetched on demand; no filename-prefix preloading is supported.

Give the responder priorities for the opening, immediate action resolution, authored dice, dialogue, sensory feedback, disclosure, and factual planning signals. It does not write sticky notes or take game actions. Ensure `RESPONDER.md` is robust and features an explicit **Lore Directory Layout** detailing subdirectories and files (`lore/characters/`, `lore/locations/`, `lore/rules/`, etc.) so the live narrator knows where canonical facts live. Give the planner priorities for contingent future developments, off-screen actors, opposition intentions, campaign pacing, and planner-owned sticky notes (such as the hidden `Upcoming Encounter Suggestion`). It never narrates a second scene or chooses dice outcomes. Give the recorder priorities for reconciling completed responses and committed player events against established facts, maintaining recorder-owned sticky notes, and taking validated Narratron game actions. Shared world facts and mechanics belong in separate lore documents accessible to all roles.

The planner begins immediately on a Player Action event, before responder completion. Treat that event as intent: research and prepare, but await the separate Responder Response event before committing costs or outcomes. Never count the two events as two actions. The planner has eight lore reads and eight searches per run; the responder has three of each.

#### Content to distribute by role

1. **Premise & Core Philosophy**:
   - The central conflict and thematic foundation.
   - *Example (Commerce / Negotiation):* Non-violent commerce where inventory is HP, eliminating combat in favor of haggling, permits, and logistics.
   - *Example (Mystery / Investigation):* Paranoia investigation where the player interacts normally with NPCs before danger strikes.
2. **Narrative Arc & Timeline Progression**:
   - Outline key milestones, acts, or in-game days.
   - *Example (3-Phase / Multi-Day Mystery Timeline):*
     - *Day / Phase 1:* Arrival, exploration, introductions, and initial gossip (no active danger yet).
     - *Day / Phase 2:* Complication or sabotage event; an NPC confides in the player; DM selects the culprit based on player actions.
     - *Day / Phase 3:* Culprit escalates and actively opposes the player; full climax and resolution.
3. **Asset & Lore Directory Roadmap**:
   - An explicit table of contents telling the LLM which subfolders exist and what they contain (e.g., `characters/`, `locations/`, `rules/`, `quests/`). This ensures the planner knows what lore tools to call.
4. **Victory & Failure Conditions**:
   - Clear, non-trivial resolution states.
   - *Example (Economic Victory):* Accumulate target profits and rare artifacts vs Bankruptcy (zero funds and no goods).
   - *Example (Survival / Mystery):* Unmask the culprit, secure an antidote/escape, or neutralize the threat.
5. **State Tracking & Sticky Note Enforcement**:
   - Explicit instructions on which sticky notes must exist and what fields they track.
   - **Crucial Rule:** Instruct the story planner to strictly update existing dedicated notes rather than creating new stickies for every minor event.
6. **DM Guidelines & Disclosure Pacing**:
   - Instructions on how quickly to reveal secrets. Don't dump twists immediately; reward player inquiry and investigation.

---

### 5.2. Character Dossier Pattern

Characters should be placed in `lore/characters/` (or dedicated subfolders). Use this standardized character profile structure:

```text
================================================================================
CHARACTER: KEEPER ORUN (ARCHIVIST OF THE SPIRE)
================================================================================
IMAGE_REFERENCE: Keeper Orun
VOICE_TAG: deep, rhythmic, brass resonance
ARCHETYPE: Melancholy Automaton / Forgotten Scholar

1. APPEARANCE & VIBE:
Seven-foot-tall brass automaton with an etched porcelain face mask and glowing amber optic lenses. Speaks with a rhythmic, deliberate cadence, punctuated by soft clicks of internal gears.

2. BACKGROUND & CURRENT SITUATION:
Has tended the celestial archives for three centuries since the mortal astronomers vanished. Deeply lonely, but bound by ancient oath to protect the inner sanctum.

3. PERSONALITY TRAITS:
- Punctual, courteous, and obsessed with proper cataloging.
- Fascinated by organic mortals; asks naive questions about eating, dreaming, and aging.
- Terrified of water corrosion and acoustic resonance pulses.

4. INTERACTION TRIGGERS (PLAYER AGENCY):
+ GREEN FLAGS (BUILDS RAPPORT):
  - Inquiring about the catalog system or celestial history.
  - Showing care for parchment or delicate instruments.
  - Offering machine oil or clean polishing cloths.
- RED FLAGS (TRIGGERS HOSTILITY):
  - Touching the harmonic crystal array without permission.
  - Referring to him as a "mindless appliance" or "broken machine".

5. SECRETS (DM-ONLY LORE):
Orun knows the third harmonic key was stolen by the rival excavation crew and hidden in the lower pumping station. He will only reveal this if player achieves high rapport.

6. SIGNATURE QUOTE:
"Three hundred years of silence, traveler, and you ask if my hinges ache. They do. But my archives endure."
```

#### Why the Character Dossier Pattern Works
- **`IMAGE_REFERENCE`**: Gives the exact asset path so the agent can immediately trigger `show_image` without guessing or hallucinating filenames.
- **`VOICE_TAG` & Quotes**: Guides the TTS and LLM character voicing engine for authentic dialogue.
- **Green Flags / Red Flags**: Gives players actionable social levers to influence the story.
- **DM Secrets**: Distinguishes public persona from private truth, preventing early spoilers.

---

### 5.3. Location & World Dossier Pattern

Locations should be organized in `lore/locations/` (optionally grouped into regional or thematic subdirectories):

```text
================================================================================
LOCATION: CELESTIAL ORRERY CHAMBER
================================================================================
IMAGE_REFERENCE: Celestial Orrery Chamber

1. OVERVIEW & ATMOSPHERE:
A cavernous dome of dark basalt with towering brass armillary spheres suspended in mid-air. Starlight filters through crystalline skylights, casting intricate geometric shadows across the marble floor. The hum of rotating brass gears fills the air.

2. INTERACTABLE ELEMENTS & LOCAL CLUES:
- The Central Astrolabe: Missing one of three harmonic prism keys. Slot shows faint violet residue.
- The Archivist's Pedestal: Contains a brass ledger written in astronomical shorthand.
- Fallen Automaton Scout: Damaged automaton in the corner; can be salvaged for 1 Brass Cog and 1 Optical Lens.

3. CHARACTERS PRESENT:
- Keeper Orun (usually found polishing the planetary rings).

4. EXITS & ROUTE HAZARDS:
- North Archway: Leads to the High Balcony (Safe passage).
- Lower Trapdoor: Leads to the Flooded Pumping Station.
  * Hazard: Corroded Rung Ladder — Climbing down requires caution; rushing risks falling into mineral-heavy water.
```

---

### 5.4. Custom Mechanics & Subsystem Lore

If your adventure features non-standard mechanics (such as trading economies, affection meters, or dice-based improvisation), document them clearly in `lore/rules/` or in the appropriate role guide, with shared mechanics in separate lore documents:

- **Economy & Inventory Pattern**:
  - Define trade goods, buy/sell values, weights, and carrying capacities.
  - Mandate specific sticky notes for tracking currency (`"Current Currency"`), inventory (`"Inventory & Quantities"`), and encumbrance (`"Caravan Weight & Capacity"`).
- **Social / Relationship & Trust Pattern**:
  - Define numerical tiers (e.g., 0–25 Hostile, 26–50 Neutral, 51–75 Warm, 76–100 Devoted / Ally).
  - Instruct the DM to track scores on a dedicated sticky note.
- **Faction Standing & Reputation Pattern**:
  - Track favors, grudges, and fulfilled tasks for competing factions on a shared standing note.

---

### 5.5. Player Death, Lethal Consequences & Restarts
- **Explicit Lethality**: When designing adventures with deadly hazards, hostile tyrants, assassinations, or lethal combat, explicitly permit player death in `theater.yaml` (`style`) and both `lore/RESPONDER.md` and `lore/PLANNER.md`.
- **No Artificial Plot Armor**: Instruct the story planner that plot armor should not protect players who take deliberately suicidal actions, drink lethal poisons, or fail unescapable ultimatums.
- **Restart on Continuation**: Clearly specify that if player death or a fatal loss condition occurs, any continued play represents a clean restart of the scenario from the beginning (as a new character, fresh incarnation, or timeline reset).
- **Death Hint**: Add instructions for the story planner to give some possibly cryptic hint for why the player died, if it wasn't obvious. The player should feel like their death was a fair possibility, and learn from
it. The instructions to provide the hint should take that into account.

---

### 5.6. Counter-Plotting & Adversarial Escalation Pattern
- **Dynamic Opposition**: When players perform exceptionally well, amass powerful leverage, or bully key NPCs/factions, the opposition must not remain passive punching bags.
- **Auditing Past Actions**: Instruct the story planner to have rival NPCs investigate the player's previous actions during the scenario (e.g., reviewing security scrying logs, checking forged signatures or seals, debriefing spies/guards, or inspecting physical crime scenes).
- **Retroactive Dirt & Standoffs**: NPCs manufacture or discover counter-dirt on the player based on earlier sloppy steps, creating dramatic counter-blackmail standoffs, mutual assured destruction, or ambush hearings before authority figures.
- **Sticky Note Tracking**: Track emerging counter-investigations on the relevant leverage or faction sticky note to keep the pressure tangible across turns.

---

## 6. Visual & Audio Peripherals

### 6.1. Visual References (`references/`)
- Place all static images in `references/`.
- Character portraits belong in `references/characters/<Character Name>/` with `character.yaml`.
- Scene and location images belong in `references/scenes/<Scene Name>/` with `scene.yaml`.
- Supported formats: `.png`, `.jpg`, `.jpeg`, `.webp`.
- **Cover Image**: Required. Referenced in `metadata.json` (`"cover_image": "references/cover.png"`) and `theater.yaml` (`starting_image: "references/cover.png"`).
- **Annotation in Lore**: Include `IMAGE_REFERENCE: <Character Name>` in character dossiers and `IMAGE_REFERENCE: <Scene Name>` in location lore documents.

### 6.2. Atmospheric Playlists (`playlists/`)
- Organize sound files into mood or scene subfolders under `playlists/`:
  ```text
  playlists/
  ├── exploration/
  │   ├── ancient_halls.mp3
  │   └── quiet_steps.mp3
  ├── tension/
  │   └── ticking_clock.mp3
  └── victory/
      └── triumphant_fanfare.mp3
  ```
- Supported formats: `.mp3`, `.wav`, `.ogg`, `.flac`.
- Prefer seamless, loopable audio with gentle intros and outros.

---

## 7. Testing & Verification Runbook

Always test adventures locally before packaging or distributing.

### 7.1. Fast CLI Smoke Test
Verify that the adventure loads, parses configuration, and completes an initial narrative turn:

```bash
uv run python testlab/adventure_runner.py --adventure <adventure-folder-name> --smoke
```

- Returns exit code `0` on success.
- Prints active sticky notes, opening agent response, and staged peripheral calls.

### 7.2. Interactive CLI Playthrough
Play through multiple turns directly in the terminal to evaluate dialogue, state updates, and pacing:

```bash
uv run python testlab/adventure_runner.py --adventure <adventure-folder-name>
```

- Type player actions at the `Player > ` prompt.
- Inspect real-time sticky note updates and peripheral tool calls (`show_image`, `play_playlist`).
- Type `reset` to restart from turn 1, or `quit` to exit.

### 7.3. Autonomous Playtesting (`--autoplay` Mode)
Stress-test pacing, edge cases, lore adherence, and sticky note stability without manual typing using the autonomous player agent:

```bash
# Basic 10-turn autonomous run:
uv run python testlab/adventure_runner.py --adventure <adventure-folder-name> --autoplay

# Custom duration and persona:
uv run python testlab/adventure_runner.py --adventure <adventure-folder-name> \
    --autoplay \
    --turns 20 \
    --autoplay-instructions "Play in a realistic style: act as a grounded, pragmatic mortal assistant prioritizing survival, diplomatic leverage, and careful protocol."
```

- **How it Works**: The `AutoPlayer` agent queries Gemini to evaluate recent scene narration and sticky notes, formulating an internal tactical **thought** and an in-character **action** each turn.
- **Output Artifacts**: Automatically generates chronological Markdown and JSON logs in `evaluation_result/autoplay_<adventure>_<timestamp>.md`.
- **Key Flags**:
  - `--autoplay`: Flag to activate autonomous play.
  - `--autoplay-instructions` / `--autoplay_instructions`: Custom persona, tactical directives, or behavioral constraints.
  - `--turns` / `-n` / `--autoplay-turns`: Turn count limit (default: 10).
  - `--autoplay-model`: Model driving player agent (default: `gemini-3.8-flash`).
  - `--autoplay-delay`: Pause between turns in seconds to pace API requests.

### 7.4. Browser Diagnostic Harness (Test Lab)
Test the visual interface and canvas peripherals locally:

```bash
uv run python -m uvicorn testlab.server:app --host 127.0.0.1 --port 8015
```

Open `http://127.0.0.1:8015/adventure-runner` in your browser to interact with the visual test harness.

### 7.5. Definitive End-to-End Test: Deploy via `/deploy`
The gold standard for validating an adventure before public release:

1. Navigate to **[narratron.app/deploy](https://narratron.app/deploy)**.
2. Drag and drop your adventure package folder (or zipped archive) onto the dropzone.
3. Click **🚀 Deploy Theater** to launch a live instance.
4. Verify:
   - Starting cover image renders immediately on the canvas.
   - Player voice or chat input produces reactive storytelling and smooth turns.
   - Sticky notes update accurately without note proliferation.
   - Reference images and playlists trigger when their associated characters, locations, or moods arise.

---

## 8. Quality & Validation Checklist

Before packaging your adventure for players or deploying to a live session:

- [ ] **Package Folder**: Stored under `adventures/<slug>/` with lowercase alphanumeric kebab-case name.
- [ ] **`metadata.json`**:
  - [ ] Valid JSON syntax.
  - [ ] `id` matches directory slug.
  - [ ] `cover_image` points to an existing file in `references/`.
  - [ ] `title`, `description`, `author`, `genre`, and `tags` are complete.
- [ ] **`theater.yaml`**:
  - [ ] Valid YAML syntax.
  - [ ] Live-agent behavior and instructions use the `live_agent` section.
  - [ ] `story_planning.adventure_mode: true` is set.
  - [ ] `starting_image` points to a valid file in `references/`.
  - [ ] `planning.yaml` defines every sticky with an explicit owner and valid initial values.
  - [ ] `story_planning.enforce_structured: true` has a nonempty `planning.yaml` contract.
- [ ] **`lore/` Architecture**:
  - [ ] Contains independent `lore/RESPONDER.md`, `lore/PLANNER.md`, and `lore/RECORDER.md` guides, with no dependency on the other private guides.
  - [ ] The responder guide defines opening, tone and resolution; the planner guide defines campaign progression and pacing; the recorder guide defines factual note reconciliation and game actions.
  - [ ] Individual character and location lore files contain explicit `IMAGE_REFERENCE: references/<file>` annotations.
  - [ ] Secrets and spoilers are isolated from public knowledge.
- [ ] **Assets**:
  - [ ] Cover image exists in `references/`.
  - [ ] Character and location reference images are properly organized under `references/characters/<Name>/` and `references/scenes/<Name>/` (`.png`, `.jpg`, `.webp`).
  - [ ] `playlists/` contain valid audio files organized by mood subdirectories.
- [ ] **Testing**:
  - [ ] CLI smoke test passes: `uv run python testlab/adventure_runner.py --adventure <slug> --smoke`.
  - [ ] Interactive multi-turn CLI test succeeds without unhandled errors.
  - [ ] Autonomous multi-turn playtest passes: `uv run python testlab/adventure_runner.py --adventure <slug> --autoplay --turns 10`.
  - [ ] Live `/deploy` session verified on [narratron.app/deploy](https://narratron.app/deploy).

---

## 9. Packaging & Distribution

Adventures are fully portable directory packages. To distribute your adventure:
- **Direct Deployment**: Drag and drop the folder or `.zip` archive into [narratron.app/deploy](https://narratron.app/deploy) for instant hosted play.
- **Local Installation**: Drop the adventure folder directly into the `adventures/` directory of any Narratron Buddy installation.
- **Package Archive**: Compress the folder into a `.zip` file for sharing on code repositories, gaming forums, or cloud drives.
