# Submitted Games Directory

This directory (`games/`) is the repository for pre-defined, submitted game packages in **Narratron**. Games in this directory can be browsed and attached directly to any theater in the Theater Builder (`/theater-editor`).

## Game Package Layout

Each game package lives in `games/<slug>/`:

```text
games/<slug>/
├── metadata.json          # Catalog card metadata (id, title, description, author, genre, tags, cover_image)
├── README.md              # Optional documentation and rules summary
├── game/
│   ├── game.yaml          # Required game declaration (or game.yml / game.json)
│   ├── panel.html         # Optional passive HTML panel
│   ├── *.svg              # Optional SVG vector assets
│   ├── *.json             # Optional companion JSON schema or data files
│   └── game_test.py       # Optional pytest unit test for the game logic
└── stamps/                # Optional stamp token images used by the game (e.g. stamps/crown.png)
```

## Example Game Package

See [`example_game/`](./example_game/) for a complete reference game package (Tic-Tac-Toe) including `game/game.yaml`, `game/panel.html`, `game/crest.svg`, `stamps/crown.png`, and `game/game_test.py`.

## Versioning and Publishing Games

The shared content command manages Git history separately from the app-served
GCS catalog:

```bash
# Snapshot local changes, commit, and push to the game Git remote:
uv run python scripts/content.py upload --content games --message "Update rules"
# Pull versioned games into the local editing folder:
uv run python scripts/content.py download --content games
# Publish the uploaded commit to the catalog served by the app:
uv run python scripts/content.py publish --content games
```

Add `--package wacky-chess` to select one package, or `--dry-run` to preview an
operation without remote access or changes. Publishing reads an uploaded Git
commit; local edits require another upload before they can be published.
Run `uv run python scripts/content.py --help` for setup and recovery instructions.

## Testing & Attaching Games

- **Run Game Unit Tests**: `uv run pytest games/<slug>/game/`
- **Interactive Game Lab**: Open `/game-lab` to test player and AI turns against your game package.
- **Attach in Theater Editor**: Open `/theater-editor`, click **＋ Game**, select your game from the library, and click **Attach Game**.
