# Example Adventure: The Library of Origins

This directory serves as the **official reference template** for building premade adventure packages in Narratron Buddy. It is an extremely brief starter example about exploring a mysterious repository of discarded story ideas and unfinished drafts.

For complete documentation on package specifications, testing with the Testlab Adventure Runner, and submitting your adventure to get featured on **narratron.app**, see:
👉 **[Writing Adventures Documentation](../../docs/writing_adventures.md)**

---

## Directory Structure Breakdown

```text
example_adventure/
├── metadata.json           # Catalog info (title, author, tags, difficulty, cover image)
├── theater.yaml            # Story planner settings, agent persona, and runtime config
├── planning.yaml           # Deep planner schemas, sticky definitions, and initial notes
├── README.md               # This reference file
├── LICENSE                 # Adventure license terms
├── animations/             # Standalone animations and video loops
│   └── book_of_origins_portal/
│       ├── video.json
│       └── video.mp4
├── game/                   # CEL checkers rules and a direct HTML panel
│   ├── game.yaml            # State, transitions, panel parameters and named actions
│   ├── panel.html           # Passive HTML/CSS board bound to CEL values
│   └── crest.svg            # Embedded panel image
├── lore/                   # Contextual lore injected into the story modules
│   ├── RESPONDER.md         # Pinned immediate-response instructions
│   ├── RECORDER.md          # Pinned factual-recording and game-control instructions
│   ├── PLANNER.md           # Pinned background-planning instructions
│   ├── characters/         # NPC dossiers
│   │   └── the_caretaker.txt
│   └── locations/          # Room layouts and sensory details
│       └── the_discarded_stacks.txt
├── references/             # Visual assets & cover art used by image tools
│   ├── characters/         # Character reference portraits
│   │   └── The Caretaker/
│   │       ├── 1.webp
│   │       └── character.yaml
│   ├── scenes/             # Scene iterations and descriptions
│   │   └── The Desk of Origins/
│   │       ├── 1.webp
│   │       └── scene.yaml
│   └── library_of_origins_cover.webp
└── playlists/              # Thematic audio folders with sound files
    ├── ambient/
    │   └── library_ambiance.mp3
    └── exploration/
        └── corridor_echoes.mp3
```

---

## Testing This Example

### Direct HTML checkers panel

Checkers uses `panel` in `game/game.yaml` and `game/panel.html` instead of a
structured `view`. Edit the HTML/CSS to change the layout. The `cells` parameter
supplies nested text, piece, selection and destination bindings; 32 named square
actions resolve selection or movement in CEL. The reset action retains its
confirmation, and square actions retain contributor permissions.

See [Authored game panels](../../docs/game_html_panels.md) for the binding syntax
and restrictions. HTML panels evaluate on the server: selecting a piece updates
the shared selection, and clicking a highlighted destination commits the move
immediately. There is no local preview or Submit step.

### Checkers victory stamp

Play Red against Narratron's Ivory in the Checkers Club panel. Winning by
capturing the final Ivory piece or blocking all Ivory moves adds a gold crown
stamp near the upper-right of the current canvas page. The stamp persists with
that page when the game panel closes or the session reloads. Crowning an
individual checker, drawing, or losing does not award it. Reset removes the
game's crown from the current page so you can repeat the test.

The declaration in `game/game.yaml` uses `canvas_api` and the move transition's
`canvas_effects` to place `stamps/crown.png` under a stable placement key.
Canvas-enabled moves commit on the server as soon as they are selected.
Keep a still-image page visible and the canvas unpinned for the stamp test.

On Ivory's turn, you can ask Narratron to concede. Its `narratron_concede` action
ends the game with Red winning, resets the board, and awards the same crown.
Concession is unavailable after a win or draw.

### 1. Automated Smoke Test
```powershell
uv run python testlab/adventure_runner.py --adventure example_adventure --smoke
```

### 2. Interactive CLI Test
```powershell
uv run python testlab/adventure_runner.py --adventure example_adventure
```

### 3. Visual Browser Test Lab
```powershell
uv run python -m uvicorn testlab.server:app --host localhost --port 8015
# Then navigate to: http://localhost:8015/adventure-runner
```

### 4. Upload & Deploy on Narratron
1. Navigate to **[narratron.app/deploy](https://narratron.app/deploy)**.
2. Drag and drop this `example_adventure` folder (or a `.zip` archive) into the asset dropzone.
3. Click **Deploy Theater** to play and test with live canvas visuals and audio!
