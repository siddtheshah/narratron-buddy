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

See [`example_game/`](./example_game/) for a complete reference game package (Crown & Corners Checkers) including `game/game.yaml`, `game/panel.html`, `game/crest.svg`, `stamps/crown.png`, and `game/game_test.py`.

## Syncing Games with GCS

- **Upload Games to GCS**:
  ```bash
  uv run python scripts/upload_games_to_gcs.py
  # Upload a single game:
  uv run python scripts/upload_games_to_gcs.py --game wacky-chess
  # Dry run:
  uv run python scripts/upload_games_to_gcs.py --dry-run
  ```
- **Download Games from GCS**:
  ```bash
  uv run python scripts/download_games_from_gcs.py
  # Download a single game:
  uv run python scripts/download_games_from_gcs.py --game wacky-chess
  ```

## Testing & Attaching Games

- **Run Game Unit Tests**: `uv run pytest games/<slug>/game/`
- **Interactive Game Lab**: Open `/game-lab` to test player and AI turns against your game package.
- **Attach in Theater Editor**: Open `/theater-editor`, click **＋ Game**, select your game from the library, and click **Attach Game**.
