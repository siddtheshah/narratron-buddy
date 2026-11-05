# Crown & Corners Checkers (Example Game)

This is the reference pre-defined game package for Narratron. It implements American Checkers (`version: 2`) with:

- **`game/game.yaml`**: Full 8×8 board state, mandatory capture rules, multi-jump continuation, king promotion, and `put_stamp` canvas victory crown effect.
- **`game/panel.html`**: Passive HTML status panel bound to `game.yaml`'s `panel` parameters and `reset` action.
- **`game/crest.svg`**: Static vector emblem rendered inside the game view and used as the package cover.
- **`stamps/crown.png`**: Stamp token placed onto the shared canvas when the player wins.
