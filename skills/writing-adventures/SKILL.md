---
name: writing-adventures
description: Comprehensive authoring guide and procedural runbook for designing, structuring lore, configuring theater.yaml, testing, and distributing interactive adventures in Narratron Buddy.
---

# Narratron Buddy Adventure Authoring Guide

This skill provides comprehensive instructions, design patterns, architectural standards, and validation workflows for authoring interactive narrative adventures in Narratron Buddy.

---

## 1. Overview & Adventure Architecture

A **Narratron Adventure** is a self-contained content package stored under `adventures/<adventure-id>/`. It turns Narratron Buddy into a multimodal interactive storytelling experience powered by Google Gemini, generative visuals, and atmospheric audio.

Every adventure package is structured around the following standard layout:

```text
adventures/<adventure-slug>/
├── metadata.json           # Catalog metadata, genre tags, and browser display attributes
├── theater.yaml            # Story planning rules, agent persona, and tool settings
├── planning.yaml           # Story planning schema, sticky definitions, fields, owners, and initial values
├── game/                   # (Optional) Declarative turn-based game; see site_docs/game_packages.md
│   ├── game.yaml           # State, CEL rules, actions, and view (game.yml or game.json also supported)
│   └── *.svg               # (Optional) Board layouts, card graphics, or token SVGs
├── README.md               # (Optional) Creator notes or developer documentation
├── lore/                   # Worldbuilding, DM screen, character dossiers, locations, and rules
│   ├── RESPONDER.md         # Pinned immediate-response instructions (narration, dialogue, dice)
│   ├── PLANNER.md           # Pinned background-planning instructions (milestones, pacing, encounters)
│   ├── RECORDER.md          # Pinned factual recording instructions (state reconciliation, game actions)
│   ├── characters/         # NPC dossiers with image references and behavior guidelines
│   ├── locations/          # Regional descriptions, local commodities, and hazards
│   ├── factions/           # Agendas, rivalries, and allegiances
│   └── rules/              # Reusable game mechanics, economies, and relationship tiers
├── references/             # Cover art, portraits, scenes, and environment concepts
│   ├── characters/         # One folder per character containing portraits and traits
│   │   └── Keeper Orun/
│   │       ├── 1.webp
│   │       └── character.yaml
│   ├── scenes/             # One folder per scene containing imagery and description
│   │   └── The Desk of Origins/
│   │       ├── 1.webp
│   │       └── scene.yaml
│   └── cover.webp          # Starting cover art
├── stamps/                 # (Optional) Tactical tokens and markers for canvas stamps
│   └── token_hero.webp
├── animations/             # (Optional) Custom animations and manifests
│   └── intro/
│       ├── video.mp4
│       └── animation.json
└── playlists/              # Thematic audio folders with sound files
    ├── ambient/
    │   ├── theme.mp3
    │   └── description.txt # (Optional) Description of playlist mood and instrumentation
    └── tension/
        ├── suspense.mp3
        └── description.txt
```

---

## 2. Quickstart: The Adventure Authoring Workflow

For adventures with a board or structured game mechanics, prefer `game/game.yaml` for human editing. Use comments and `|-` for multiline CEL expressions, and keep CEL literal quotes in view props (`text: '"Play"'`). Keep exactly one declaration (`game.yaml`, `game.yml`, or `game.json`) in `game/`. Follow `site_docs/game_packages.md` for contracts and validation; the example adventure includes commented checkers.

When creating a new adventure, follow this sequence:

1. **Scaffold the Package**: Create `adventures/<adventure-slug>/` with `lore/`, `references/characters/`, `references/scenes/`, `references/`, and `playlists/` folders.
2. **Author `metadata.json`**: Configure the display title, slug ID, genre, difficulty, player count, and cover image.
3. **Configure `theater.yaml` & `planning.yaml`**: Set `adventure_mode: true` and configure live-agent persona, art, and music direction in `theater.yaml`. Define structured sticky notes, field schemas, explicit ownership (`owner: recorder` or `owner: planner`), and initial values in `planning.yaml`.
4. **Write Independent Role Guides**: Put opening scene, tone, action-resolution rules, and dice rules in `lore/RESPONDER.md`; put campaign milestones, hidden consequences, and planner sticky tracking in `lore/PLANNER.md`; put factual note reconciliation and game actions in `lore/RECORDER.md`. Each module has only its own complete guide pinned and cannot read the others. Put shared mechanics and facts in separate lore documents.
5. **Flesh Out Modular Lore**: Create character dossiers, location profiles, and faction files. Annotate characters with their canonical name (`image_reference: Keeper Orun`); annotate locations with their canonical scene name (`image_reference: The Desk of Origins`); other assets can use `image_reference: references/<filename>`.
6. **Add Assets**:
   - Place character portraits in `references/characters/<Character Name>/1.webp` with `character.yaml` (each character requires its own subfolder; loose images directly under `references/characters/` are unsupported).
   - Place scene imagery in `references/scenes/<Scene Name>/1.webp` with `scene.yaml` (each scene requires its own subfolder; loose images directly under `references/scenes/` are unsupported).
   - Place cover/location references in `references/`, optional tactical tokens in `stamps/`, and loopable tracks in `playlists/<Mood>/`.
   - **Asset Lifecycle**: At session initialization, the manager reads authored portraits, scene imagery, and trait files directly from `references/` without copying or duplicating them into output. During play, visual evolutions and appearance changes create new numbered iterations under `output/artifacts/updated_references/characters/` and `output/artifacts/updated_references/scenes/`. The live agent uses `<Character Name>` tags in visual prompts to bind the latest portrait. Authors can manually copy session updates back into authored folders if permanent evolution is desired.
7. **Test Locally**: Run unit tests (`uv run pytest adventures/<adventure-slug>`), smoke tests (`--smoke`), and interactive/autoplay sessions using `testlab/adventure_runner.py`.
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
  "cover_image": "references/cover.webp",
  "difficulty": "Medium",
  "recommended_players": "1-4"
}
```

### Field Requirements & Defaults
- **`id`** *(string, required)*: Lowercase alphanumeric kebab-case slug matching the folder name (e.g., `obsidian-spire`).
- **`title`** *(string, required)*: Display title in the adventure browser.
- **`description`** *(string, required)*: 1–3 sentence hook summarizing the adventure premise.
- **`author`** *(string, required)*: Creator handle or author name.
- **`genre`** *(string, required)*: Primary genre (e.g., `"Dark Fantasy"`, `"Arctic Thriller"`, `"Historical Trade"`).
- **`tags`** *(array of strings)*: Discoverability and filtering tags.
- **`cover_image`** *(string, required)*: Relative path to cover art (e.g., `"references/cover.webp"`). If omitted or missing, the adventure loader falls back to the first available image file (`.webp`, `.png`, `.jpg`, `.jpeg`) found in the package.
- **`difficulty`** *(string, optional)*: `"Easy"`, `"Medium"`, or `"Hard"` (default fallback: `"Medium"`).
- **`recommended_players`** *(string, optional)*: e.g. `"1"`, `"1-4"`, `"2-6"` (default fallback: `"1-4"`).
- **`created_at`** *(string, optional)*: ISO 8601 UTC timestamp string (e.g. `"2026-09-07T12:00:00Z"`).

---

## 4. Theater & Planning Configuration

Adventures use `theater.yaml` to govern agent persona, art direction, and runtime flags, and `planning.yaml` to define sticky note contracts and deep planning schemas.

### 4.1. Standard Adventure Theater Template (`theater.yaml`)

```yaml
live_agent:
    special_instructions: "Carry user actions faithfully and guide the adventure with dramatic tension and clear consequences. Respect player agency and describe sensory feedback vividly."
    enable_regular_observability: true

# Starting reference image displayed when a new canvas initializes
starting_image: "references/cover.webp"

visuals:
    cycle_length: 8                  # Default: 8 seconds minimum screen time per visual
    style: "dark fantasy matte painting, glowing runes, cinematic lighting, moody atmospheric fog, high detail"
    safety_reduction: false

image_generation:
    enabled: true
    cooldown_duration: 9             # Default: 9 seconds cooldown between image tool calls
    exclude_starter: true            # Hide starting_image from the live agent's image browser
    reference_wait_timeout: 30       # Seconds to wait for background reference generation

music:
    use_generated_music: false       # Set true for AI music generation; false uses premade playlists
    generation_cooldown: 90          # Seconds between generative music requests
    switch_cooldown: 15              # Seconds between playlist track switches
    style: "orchestral fantasy ambiance, haunting cello melodies, distant percussion, loopable background"

story_planning:
    adventure_mode: true             # MANDATORY: Enables player action resolution & turn tracking
    enforce_structured: true         # Require sticky contracts in planning.yaml and validate updates
    auto_begin: true                 # Automatically initiates opening scene without waiting for player input
    enable_dice: true                # Allows responder action dice and planner procedural rolls
    character_voicing: false         # Set true to synthesize distinct application voices for NPC dialogue
    text_beautification: true        # Polishes narrative output with dynamic visual text effects
    style: "engaging mystery with player agency, dramatic tension, and fair consequences"
    input_cycle_duration: 15         # Batch input window in seconds (default: 15; 0 for immediate)
    output_cycle_duration: 15        # Scene display duration in seconds (default: 15; 0 for immediate)
    require_user_input: true

chat:
    cooldown_duration: 10            # Seconds between chat responses (default: 10)

speech:
    voice_language_code: "en-US"
    accent_augmentation: true        # Amplifies character accent voice tags into explicit delivery instructions
    gender_augmentation: true        # Amplifies character gender voice tags into explicit delivery instructions

user_help:
    enabled: true
    cooldown_duration: 15
    max_output_tokens: 1200
```

### Understanding `theater.yaml` Defaults & Sections
- **`live_agent`**: Always use `live_agent` for live agent settings. The obsolete `agent` and `agent_internal` sections are no longer supported.
- **`story_planning` Timing**: Story planning uses `input_cycle_duration` (batching user inputs) and `output_cycle_duration` (scene display duration), **not** `cooldown_duration`.
- **`enforce_structured`**: When `enforce_structured: true` (standard for Adventure Mode), `planning.yaml` must be present and nonempty. All declared topics are required and pinned in contract order. Freeform notes are prohibited. When set to `false`, the recorder and planner may also create and update shared freeform topics without contract entries, while declared topics retain their schema and ownership.
- **`notepad` Section Ignored in Adventure Mode**: The top-level `notepad` section (`initial_stickies`, `max_stickies`, `required_stickies`, `hidden_stickies`) applies solely to normal narration theaters. In Adventure Mode, all sticky contracts and visibility are configured in `planning.yaml`.
- **`game_tool`**: `game_tool.enabled` is off by default and is only needed for live-agent game play outside Adventure Mode. In Adventure Mode, declarative games authored under `game/` are managed through the story orchestration engine and the recorder role.

---

### 4.2. Deep Planning Schema Template (`planning.yaml`)

Adventures use `planning.yaml` to define persistent state, structured field schemas, and ownership across the story orchestration roles.

```yaml
# Deep Planner Schema: planning.yaml
# All field descriptions, initial values, and sticky values are strictly strings. No arrays, numbers, or complex JSON Schema objects.

"Player Character":
  owner: recorder
  description: "Established player identity, objective, and physical condition."
  fields:
    name:
      description: "Character name"
      initial: "Unnamed Explorer"
    objective:
      description: "Current primary goal"
      initial: "Reach the Spire's apex"
    inventory:
      description: "Key items carried"
      initial: "Crystal lodestone"
    condition:
      description: "Physical and mental status"
      initial: "Healthy"

"Spire Security Level":
  owner: recorder
  description: "Alert level and defense automaton activity."
  fields:
    alert:
      description: "Current alert level"
      initial: "Green (Unnoticed)"
    automatons:
      description: "Automaton patrol status"
      initial: "Dormant"

"Known Clues":
  owner: recorder
  hidden: true
  description: "Discovered clues and historical hints."
  initial: "The Spire activates only when three harmonic keys are aligned."

"Upcoming Encounter Suggestion":
  owner: planner
  hidden: true
  description: "Upcoming narrative dilemma, obstacle, or opportunity staged by the story planner."
  fields:
    encounter:
      description: "Next staged encounter or anomaly"
      initial: "A deactivated sentinel automaton blocking the narrow stairwell"
    story_goal:
      description: "Meaningful choice or objective for the player"
      initial: "Choose whether to power it with a lodestone or bypass via the exterior ledge"
    drama:
      description: "Dramatic stakes or consequences"
      initial: "Exterior winds risk losing carried supplies; waking it may sound the alarm"
```

### Critical Rules for `planning.yaml`
1. **Explicit Owner on Every Sticky**: Every sticky note MUST declare either `owner: recorder` (for established facts, inventory, location, status, and committed game events) or `owner: planner` (for contingent future events, upcoming dilemmas, and opposition intentions). Mixed topics are prohibited; split mixed factual and planning topics into separate notes.
2. **String-Only Values**: Every field description and initial value must strictly be a string. Numeric and boolean values must be quoted strings (`initial: "0"`, `initial: "false"`).
3. **Structured vs. Flat Topics**:
   - **Structured Topics (with `fields`)**: Each field under `fields` has a `description` string and an `initial` string (defaulting to `"undefined"` if omitted). Do **not** place a top-level `initial` mapping on structured topics.
   - **Flat Topics (without `fields`)**: Use an optional top-level string `initial: "..."` (defaulting to `"undefined"` if omitted).
4. **Display Templates (`render`)**: `render` is optional. By default, nonempty fields render in declaration order as `field_name: value | next_field: value`. To customize layout, provide a format template such as `render: "Name: {name} | Objective: {objective}"` matching the topic's field keys.
5. **Visibility (`hidden`)**: Set `hidden: true` on notes intended for background tracking or story staging. Hidden notes are accessible to all three roles in prompt context, but remain hidden in the canvas player view until toggled.
6. **Mandatory Planner Sticky**: Every adventure should include the planner-owned hidden sticky `"Upcoming Encounter Suggestion"` (`owner: planner`, `hidden: true`) with fields `encounter`, `story_goal`, and `drama` to continuously stage narrative obstacles and dramatic pacing.

---

## 5. Lore Architecture & Design Patterns

The `lore/` folder is the core storytelling engine. Proven architectural patterns for structuring interactive adventure lore ensure high narrative consistency, deep player agency, and reliable tool calling.

### 5.1. Pinned Role Instructions: `RESPONDER.md`, `PLANNER.md`, and `RECORDER.md`

Every adventure requires all three files at the top level of `lore/`. The runtime pins the full `RESPONDER.md` only for the responder, the full `PLANNER.md` only for the planner, and the full `RECORDER.md` only for the recorder. **None of the modules can list, read, or search the others' guides.** Other `.txt` and `.md` lore files in the folder are indexed and fetched on demand; no filename-prefix preloading is supported.

| Role | Guide File | Responsibilities & Boundaries | Call Budget per Run | Authorized Writes |
| :--- | :--- | :--- | :--- | :--- |
| **Responder** | `lore/RESPONDER.md` | Owns immediate action resolution, opening scenes, narrative prose, NPC dialogue, authored dice rolls, and scene sensory feedback. Emits `planning_signals`. Does **not** write sticky notes or take game actions. | **3 reads, 3 searches** | Narration, dialogue, planning signals (no direct sticky writes) |
| **Planner** | `lore/PLANNER.md` | Owns campaign milestones, pacing, opposition intentions, off-screen clocks, and planner-owned stickies (e.g., `Upcoming Encounter Suggestion`). Reads game state and action schemas. Does **not** mutate game state or roll responder dice. | **8 reads, 8 searches** | Planner-owned stickies (`owner: planner`) |
| **Recorder** | `lore/RECORDER.md` | Reconciles completed responses and committed events against established facts. Owns recorder-owned stickies and executes validated Narratron game actions through the game runtime. | **4 reads, 4 searches** | Recorder-owned stickies (`owner: recorder`), game state mutations |

#### Timing & Action Resolution Loop
The planner begins immediately on a Player Action event, before responder completion. Treat that initial event as unresolved intent: research and prepare, but await the separate Responder Response event before committing costs, clocks, or consequences. Never count the two events as two player actions. The recorder runs after the responder to reconcile completed turns, update factual notes, and take necessary game actions.

#### Content to Distribute by Role
1. **Premise & Core Philosophy**: Include central conflict and thematic foundation in all three guides.
2. **Narrative Arc & Timeline Progression**: Put act progression, multi-day milestones, and escalation in `lore/PLANNER.md`.
3. **Lore Directory Layout**: Put an explicit table of contents in `lore/RESPONDER.md` and `lore/PLANNER.md` detailing subdirectories (`characters/`, `locations/`, `rules/`, `factions/`) so roles know where canonical facts live.
4. **Victory & Failure Conditions**: Clear, non-trivial resolution states in `lore/PLANNER.md` and `lore/RECORDER.md`.
5. **State Tracking Instructions**: Explicit instructions on which sticky notes each role maintains, reminding them to update existing dedicated notes rather than creating new stickies for minor events.
6. **DM Guidelines & Disclosure Pacing**: Guidance on pacing revelations, rewarding player inquiry, and avoiding early spoilers.

---

### 5.2. Character Dossier Pattern

Characters should be placed in `lore/characters/` (e.g., `lore/characters/keeper_orun.txt`). Use this standardized character profile structure:

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
- **`IMAGE_REFERENCE`**: Matches the canonical character folder name under `references/characters/`, allowing the live agent to bind `<Keeper Orun>` in generative prompts.
- **`VOICE_TAG` & Quotes**: Guides character voicing and speech augmentation for authentic dialogue delivery.
- **Green Flags / Red Flags**: Gives players concrete social levers to influence the story.
- **DM Secrets**: Distinguishes public persona from private truth, preventing early spoilers.

---

### 5.3. Location & World Dossier Pattern

Locations should be organized in `lore/locations/` (e.g., `lore/locations/celestial_orrery.txt`):

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
  - Mandate specific recorder-owned sticky notes for tracking currency (`"Current Currency"`), inventory (`"Inventory & Quantities"`), and encumbrance (`"Caravan Weight & Capacity"`).
- **Social / Relationship & Trust Pattern**:
  - Define numerical tiers (e.g., 0–25 Hostile, 26–50 Neutral, 51–75 Warm, 76–100 Devoted / Ally).
  - Instruct the recorder to track scores on a dedicated sticky note.
- **Faction Standing & Reputation Pattern**:
  - Track favors, grudges, and fulfilled tasks for competing factions on a shared standing note.

---

### 5.5. Player Death, Lethal Consequences & Restarts
- **Explicit Lethality**: When designing adventures with deadly hazards, hostile tyrants, assassinations, or lethal combat, explicitly permit player death in `theater.yaml` (`style`), `lore/RESPONDER.md`, and `lore/PLANNER.md`.
- **No Artificial Plot Armor**: Instruct the story planner that plot armor should not protect players who take deliberately suicidal actions, drink lethal poisons, or fail unescapable ultimatums.
- **Restart on Continuation**: Clearly specify that if player death or a fatal loss condition occurs, any continued play represents a clean restart of the scenario from the beginning.
- **Death Hint**: Instruct the story planner to provide a cryptic hint explaining why the player died if it was not obvious, helping the player learn from the outcome.

---

### 5.6. Counter-Plotting & Adversarial Escalation Pattern
- **Dynamic Opposition**: When players perform exceptionally well, amass powerful leverage, or bully key NPCs/factions, the opposition must not remain passive punching bags.
- **Auditing Past Actions**: Instruct the story planner to have rival NPCs investigate the player's previous actions during the scenario (e.g., reviewing security logs, checking forged signatures or seals, debriefing spies/guards, or inspecting physical crime scenes).
- **Retroactive Dirt & Standoffs**: NPCs manufacture or discover counter-dirt on the player based on earlier sloppy steps, creating dramatic counter-blackmail standoffs or ambush hearings.
- **Sticky Note Tracking**: Track emerging counter-investigations on the relevant leverage or faction sticky note to keep the pressure tangible across turns.

---

## 6. Visual, Tactical & Audio Peripherals

### 6.1. Character Visuals (`references/characters/`)
Each character requires its own subfolder named after the canonical character:
```text
references/characters/Keeper Orun/
├── 1.webp
├── 2.webp               # Optional visual evolution iterations
└── character.yaml       # Canonical character traits
```

Add `character.yaml` alongside the portraits to initialize traits:
```yaml
description: "A seven-foot-tall brass automaton with an etched porcelain face mask"
personality: "Punctual, curious, and nostalgic"
motivation: "Protect the celestial archives until mortal astronomers return"
quirk: "Clicks internal gears when intrigued"
gender: nonbinary        # male, female, or nonbinary
voice_tags: [deep, brass, rhythmic]
```

- **Supported Formats**: `.webp` (preferred), `.png`, `.jpg`, `.jpeg`, `.gif`. Loose files directly under `references/characters/` are unsupported.
- **Iteration Numbering**: Numbered iterations (`1.webp`, `2.webp`) allow visual evolution across story arcs. The highest numbered iteration is active.
- **Visual Prompt Tagging**: The live agent tags `<Keeper Orun>` in generative prompts to automatically attach the character's active portrait without manual path management.

### 6.2. Scene Visuals (`references/scenes/`)
Each scene requires its own subfolder named after the canonical scene:
```text
references/scenes/The Desk of Origins/
├── 1.webp
├── 2.webp               # Optional visual evolution iterations
└── scene.yaml           # Canonical scene description
```

Add `scene.yaml` beside the scene images:
```yaml
description: "An ancient cedar drafting table covered in star charts, glowing inkwells, and brass astrolabes"
```

- **Supported Formats**: `.webp` (preferred), `.png`, `.jpg`, `.jpeg`, `.gif`. Loose files directly under `references/scenes/` are unsupported.
- **Iteration Numbering**: Numbered iterations (`1.webp`, `2.webp`) allow scenes to change state (e.g. ruined, night, festive).

### 6.3. Static References & Tactical Stamps
- **General References (`references/`)**: Place cover artwork, maps, and item sketches directly under `references/` (e.g., `references/cover.webp`).
- **Tactical Stamps (`stamps/`)**: Place stamp tokens and tactical markers under `stamps/` (e.g., `stamps/token_sentinel.webp`). Supported formats: `.webp`, `.png`, `.jpg`, `.jpeg`, `.gif`.
- **Animations (`animations/`)**: Package pre-rendered animation clips or manifests under `animations/<id>/` (e.g., `animations/intro/video.mp4`, `animations/intro/animation.json`).

### 6.4. Atmospheric Playlists (`playlists/`)
Organize sound files into mood or scene subfolders under `playlists/`:
```text
playlists/
├── exploration/
│   ├── ancient_halls.mp3
│   ├── quiet_steps.mp3
│   └── description.txt  # Optional: "Gentle ambient cello with quiet flute echoes"
├── tension/
│   ├── ticking_clock.mp3
│   └── description.txt  # Optional: "Slow rhythmic heartbeat and dissonant strings"
└── victory/
    └── triumphant_fanfare.mp3
```
- **Supported Formats**: `.mp3`, `.wav`, `.ogg`, `.flac`, `.m4a`, `.aac`.
- **Description File**: Add optional `description.txt` to help agents select suitable mood tracks.
- Prefer seamless, loopable audio with gentle intros and outros.

---

## 7. Testing & Verification Runbook

Always test adventures locally before packaging or distributing.

### 7.0. Adventure Unit Tests (Run Only These)
Default test discovery (`uv run pytest`) excludes `adventures/`. When writing or editing an adventure with unit tests, run **only** that adventure's unit tests:

```bash
uv run pytest adventures/<adventure-folder-name>
```

- Do not run the full core test suite (`uv run pytest`) or tests from other adventures when developing an adventure.
- Keep adventure unit tests isolated inside the adventure folder (e.g., `game/*_test.py` or `tests/`).

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

#### How Autoplay Works
- The `AutoPlayer` agent queries Gemini to evaluate recent scene narration, spoken dialogue, and available game actions (if a declarative game exists under `game/`).
- Each turn produces an internal tactical **thought** and an in-character **action** (or declarative game control).
- By default, sticky notes are hidden from the player to prevent metagaming. Pass `--autoplay-include-stickies` to expose active stickies to the player agent.
- **Output Artifacts**: Session chronicles and structured JSON summaries are automatically saved in `evaluation_results/autoplay_<adventure>_<timestamp>.md` and `.json`.

#### CLI Flags Reference

| Flag | Aliases | Default | Description |
| :--- | :--- | :--- | :--- |
| `--adventure` | — | `example_adventure` | Adventure folder name or relative path. |
| `--smoke` | — | `False` | Run a single automated smoke action and exit. |
| `--autoplay` | — | `False` | Enables autonomous player mode. |
| `--turns` | `-n`, `--autoplay-turns`, `--autoplay_turns` | `10` | Maximum number of turns to execute in autoplay mode. |
| `--autoplay-instructions` | `--autoplay_instructions` | `""` | Directives, persona, or behavioral constraints for the player agent. |
| `--autoplay-model` | `--autoplay_model` | `gemini-3.8-flash` | Gemini model ID driving the autonomous player agent. |
| `--autoplay-log` | `--autoplay_log`, `--log-file`, `--log_file` | `evaluation_results/...` | Destination path for the session log file (`.md` and `.json`). |
| `--autoplay-delay` | `--autoplay_delay` | `0.0` | Pause in seconds between turns to pace API requests. |
| `--autoplay-include-stickies` | `--autoplay_include_stickies` | `False` | Expose active sticky notes in the player agent's prompt context. |
| `--action` | — | `""` | Optional single action to execute, or override starting action for turn 1. |
| `--agent-model` | — | `gemini-3.8-flash` | Model ID for the live text agent. |
| `--planner-model` | — | `gemini-3.8-flash` | Model ID for the story planner and deep planning engine. |
| `--nodes` | — | `3` | Nodes ahead buffer for story planning. |

### 7.4. Browser Diagnostic Harness (Test Lab)
Test the visual interface, interactive canvas, and game surfaces locally:

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
   - Reference images, tactical stamps, and playlists trigger when their associated characters, locations, or moods arise.

---

## 8. Quality & Validation Checklist

Before packaging your adventure for players or deploying to a live session:

- [ ] **Package Folder**: Stored under `adventures/<slug>/` with lowercase alphanumeric kebab-case name.
- [ ] **`metadata.json`**:
  - [ ] Valid JSON syntax.
  - [ ] `id` matches directory slug.
  - [ ] `cover_image` points to an existing image in `references/`.
  - [ ] `title`, `description`, `author`, `genre`, and `tags` are complete.
- [ ] **`theater.yaml`**:
  - [ ] Valid YAML syntax.
  - [ ] Live-agent behavior and instructions use the `live_agent` section.
  - [ ] `story_planning.adventure_mode: true` is set.
  - [ ] `starting_image` points to a valid file in `references/`.
  - [ ] Timing parameters use `input_cycle_duration` and `output_cycle_duration` (no `cooldown_duration` under `story_planning`).
- [ ] **`planning.yaml`**:
  - [ ] Every sticky declares an explicit owner (`owner: recorder` or `owner: planner`).
  - [ ] Structured topics define fields with string `description` and `initial` values (no topic-level `initial`).
  - [ ] Flat topics use string `initial` values.
  - [ ] Includes hidden planner sticky `"Upcoming Encounter Suggestion"` (`owner: planner`, `hidden: true`) with fields `encounter`, `story_goal`, `drama`.
  - [ ] `story_planning.enforce_structured: true` has a nonempty contract.
- [ ] **`lore/` Architecture**:
  - [ ] Contains independent `lore/RESPONDER.md`, `lore/PLANNER.md`, and `lore/RECORDER.md` guides.
  - [ ] Role boundaries are respected: responder owns immediate narration, dialogue, and dice; planner owns campaign milestones and pacing; recorder owns factual note reconciliation and game actions.
  - [ ] Each role guide is standalone; no module depends on reading private peer guides.
  - [ ] Shared lore files (`characters/`, `locations/`, `rules/`, `factions/`) contain reusable canon and mechanics.
- [ ] **Assets**:
  - [ ] Cover image exists in `references/`.
  - [ ] Character folders in `references/characters/<Name>/` include numbered portraits (`1.webp`) and `character.yaml`.
  - [ ] Scene folders in `references/scenes/<Name>/` include numbered visuals (`1.webp`) and `scene.yaml`.
  - [ ] `playlists/` contain valid audio files organized by mood subdirectories, with optional `description.txt`.
- [ ] **Testing**:
  - [ ] Adventure unit tests pass (if present): `uv run pytest adventures/<slug>`.
  - [ ] CLI smoke test passes: `uv run python testlab/adventure_runner.py --adventure <slug> --smoke`.
  - [ ] Interactive multi-turn CLI test succeeds without unhandled errors.
  - [ ] Autonomous multi-turn playtest passes: `uv run python testlab/adventure_runner.py --adventure <slug> --autoplay --turns 10`.
  - [ ] Logs in `evaluation_results/` show clean sticky note updates without note sprawl.
  - [ ] Live `/deploy` session verified on [narratron.app/deploy](https://narratron.app/deploy).

---

## 9. Packaging & Distribution

Adventures are fully portable directory packages. To distribute your adventure:
- **Direct Deployment**: Drag and drop the folder or `.zip` archive into [narratron.app/deploy](https://narratron.app/deploy) for instant hosted play.
- **Local Installation**: Drop the adventure folder directly into the `adventures/` directory of any Narratron Buddy installation.
- **Package Archive**: Compress the folder into a `.zip` file for sharing on code repositories, gaming forums, or cloud drives.
