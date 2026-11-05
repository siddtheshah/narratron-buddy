# Tic-Tac-Toe (Example Game)

This is the reference pre-defined game package for Narratron. It implements classic 3×3 Tic-Tac-Toe (`version: 2`) with:

- **`game/game.yaml`**: Full 3×3 board state, win/draw detection across all rows, columns, and diagonals, Narratron opponent moves and concessions, and `put_stamp` canvas victory crown effect.
- **`game/panel.html`**: Passive HTML status panel with an interactive 3×3 grid bound to `game.yaml`'s `panel` parameters and `reset` action.
- **`game/crest.svg`**: Static vector emblem rendered inside the game view and used as the package cover.
- **`stamps/crown.png`**: Stamp token placed onto the shared canvas when the player wins.
- **`game/game_test.py`**: Unit tests verifying board mechanics, turns, victories, concessions, draws, and stamp persistence.
