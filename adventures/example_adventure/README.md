# Example Adventure: The Clockwork Archive

This directory serves as the **official reference template** for building premade adventure packages in Narratron Buddy.

For complete documentation on package specifications, testing with the Testlab Adventure Runner, and submitting your adventure to get featured on **narratron.app**, see:
👉 **[Writing Adventures Documentation](../../docs/writing_adventures.md)**

---

## Directory Structure Breakdown

```text
example_adventure/
├── metadata.json           # Catalog info (title, author, tags, difficulty, cover image)
├── theater.yaml            # Story planner settings, agent persona, and sticky notes
├── README.md               # This reference file
├── lore/                   # Contextual lore injected into the story planner
│   ├── readfirst_overview.txt # High-level guide persisted in story context
│   ├── factions/           # Factions, NPCs, and motives
│   │   └── the_archivists.txt
│   └── locations/          # Room layouts, puzzles, and sensory details
│       └── great_library.txt
├── references/             # Visual assets & cover art used by image tools
│   ├── clockwork_archive_cover.png
│   └── keeper_orun.png
└── playlists/              # Thematic audio folders with sound files
    ├── ambient/
    │   └── clockwork_ambiance.mp3
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
