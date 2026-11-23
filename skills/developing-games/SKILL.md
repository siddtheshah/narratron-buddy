---
name: developing-games
description: Design mechanics, state, and actions for Narratron turn-based games, then implement and playtest CEL game packages, panels, and Narratron opponents.
---

# Develop Narratron games

## Design the mechanics, state, and actions

For a new game or a change to the rules, sketch the affected mechanics before editing the declaration. For a panel-only change, read the existing state and action contracts to see what the controls can do.

1. **Define the core loop.** Use the contributor's premise and existing rules to identify the players, their meaningful choices, how a round starts and ends, and what counts as a win, draw, or loss. Decide when control passes or when both sides must agree. Walk through a normal turn and an edge case with concrete values. Play must be able to pause at a stable state between actions.
2. **Represent a position as a snapshot.** Write an example initial state and the snapshot after one move. Record only what the next move needs, such as the board, active actor, resources, pending terms, or terminal result. Define each field's type, bounds, and initial value in `state_schema` and `initial`. Put fixed rules and lookup tables in `constants` and compute values such as legal targets in `derived` when they follow from the saved state. Decide which information the player and Narratron each need to see.
3. **Map each choice to a transition.** For each action, write down who can invoke it, its inputs, the conditions that make it legal, the resulting fields, and whether it hands off control or triggers Narratron. Include setup, reset, or cancellation where the rules call for them. Work through at least one rejected action and one terminal state before building the panel. Use these cases as the first checks in the runtime.

In the bundled Tic-Tac-Toe, the rules track a nine-cell board, the current turn, and the outcome. From an empty board with `turn: 1`, the player's `move` with `index: 4` changes the board to `[0, 0, 0, 0, 1, 0, 0, 0, 0]` and the turn to `-1` for O. The action requires the player's turn, an empty square, and an unfinished game. Trying square 4 again leaves the state unchanged. A move that completes a line records the winner and ends play.

## Choose a package and reference

- For a reusable game, work in `games/<slug>/` with `metadata.json` and `game/game.yaml`. For a game tied to a specific adventure or theater, work in that package's `game/` directory. Keep one declaration there (`game.yaml`, `game.yml`, or `game.json`).
- Start with the [package contract](../../site_docs/game_packages.md) and [Tic-Tac-Toe](../../games/example_game/README.md). Use the [example adventure's Checkers](../../adventures/example_adventure/game/game.yaml) for piece selection and passing turns to Narratron.
- Read the [HTML panel contract](../../site_docs/game_html_panels.md) for a custom `game/panel.html` and the [canvas interface](../../site_docs/game_canvas_interface.md) for actions that read or write the shared canvas. Use the declarative view for ordinary controls. Authored HTML uses the runtime's bindings and actions.

## Build a playable turn

Implement and check one complete turn before adding polish. Enforce turn ownership, move legality, and any required acceptance in CEL guards, with invariants for conditions that must hold in every committed state. Each transition returns the complete next state. Keep model instructions and `model_state` useful for choosing among available Narratron actions. Quote CEL expressions in view props, including literal strings such as `text: '"Play"'`.

Use non-triggering actions for selection and exploration. With `local_preview: true`, test the draft and its Undo, Discard, or Submit path where present. Check that other viewers see only committed state. Keep reset and game-over behavior in the game declaration.

## Playtest locally

From the repo root, install dependencies once per checkout with `uv sync --locked` and `npm ci`. Then run:

```powershell
npm run build
uv run python -m uvicorn testlab.server:app --host 127.0.0.1 --port 8015
```

For automated browser checks, install Chromium with `npx playwright install chromium` if it is missing.

1. Open [Game Lab](http://127.0.0.1:8015/game-lab). Enter the workspace-relative declaration path in **Game package (workspace path)**. The field suggests games under `games/`, `adventures/`, and `theaters/` and also accepts a path typed directly.
2. Turn off **Enable AI play** for a provider-free first pass, then click **Load fresh game**. Play a meaningful move, check the expected state and revision under **Game state & AI responses**, and try a rejected move or disabled control. Use the manual Narratron buttons where they supply the needed arguments. For other argument shapes, drive the action in a package test using the production `GameState`. Try the role buttons if the game allows contributors to act.
3. Click **Load fresh game** after edits. The lab snapshots each package when it loads. Use **Download session transcript** for turn-by-turn evidence and **Close session** to release the temporary state.

For a known-good first move, load `adventures/example_adventure/game/game.yaml` with AI play off. Move the leftmost red piece on the front occupied row from A3 to B4 (squares 40 and 33). The panel should show **Ivory to move** and the lab should report revision 2.

For a live opponent, enable AI play with a configured text provider. Use [scenario replay](../../testlab/README.md#game-testing-lab) when you need repeatable inputs and a JSON report. Game Lab uses an empty canvas and exercises model action selection directly. For shared stamps, real collaboration, or mobile Watch, attach the game with **＋ Game** in a disposable local theater, then open **Save & test →**. Start the app with `uv run main.py --testing_use_local` and open [Theater Builder](http://localhost:8000/theater-editor). Reattach after editing the source package because the editor copies its assets.

For an embedded game that drives narration, use [Adventure Runner](http://127.0.0.1:8015/adventure-runner) and its **Game** tab with the required providers configured. Author game-specific story guidance in `lore/GAME.md` and check the resulting game action and narration in session history. See the [StoryGameModule ADR](../../components/story/adr/adr-002-story-game-module.md) for the adventure flow. For live-agent play outside Adventure Mode, enable the game's live-agent option in the editor or set `game_tool.enabled: true`.

## Leave a playable handoff

- Run the authored package's tests when present. The [example game tests](../../games/example_game/game/game_test.py) show how to verify turn order, invalid moves, win/draw/reset, and canvas effects. For changes to the shared lab or runtime, run the relevant checks below.

  ```powershell
  uv run pytest -n 0 games/example_game/game testlab/labs/game_lab_test.py testlab/labs/game_harness_test.py
  npm run test:ui -- testlab/frontend/labs/game-lab_test.ts frontend/game/game-renderer_test.ts
  ```

- Open the actual package in a browser, play a meaningful turn, and capture the board in a useful state. Give the contributor the package path, a local launch URL and exact first moves, and a screenshot or transcript if available. State which paths you exercised and call out any provider-backed or canvas behavior you could not verify.
- Keep local authoring and preview independent of [catalog publishing](../../games/README.md#versioning-and-publishing-games). New packages under `games/` are ignored by this app repo by default. Check `git status --ignored --short -- games/<slug>` if the task includes handing over a Git change.
