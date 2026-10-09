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
├── lore/                   # Contextual lore injected into the story planner
│   ├── readfirst_overview.txt # High-level DM guide persisted in story context
│   ├── characters/         # NPC dossiers
│   │   └── the_caretaker.txt
│   └── locations/          # Room layouts and sensory details
│       └── the_discarded_stacks.txt
├── references/             # Visual assets & cover art used by image tools
│   ├── characters/         # Character reference portraits
│   │   └── The Caretaker/
│   │       ├── 1.jpg
│   │       └── character.yaml
│   ├── scenes/             # Scene iterations and descriptions
│   │   └── The Desk of Origins/
│   │       ├── 1.png
│   │       └── scene.yaml
│   └── library_of_origins_cover.jpg
└── playlists/              # Thematic audio folders with sound files
    ├── ambient/
    │   └── library_ambiance.mp3
    └── exploration/
        └── corridor_echoes.mp3
```

---

## Testing This Example

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
