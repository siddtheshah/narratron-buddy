# Narratron Test Lab

Run the two browser diagnostics without starting the full Narratron app:

```powershell
python -m uvicorn testlab.server:app --host 127.0.0.1 --port 8015
```

Open `http://127.0.0.1:8015/` for the Test Lab index. It links to microphone/VAD testing, image effects, image/music/text provider benchmarks, and the asynchronous story-planner bridge.

The Music Provider Bench can run a regular generator or the **TEST ONLY: Generate base + audio adapter** fixture. Select Lyria as the base and Stable Audio 3 Small Music Base A2A (FAL) as the adapter, then use the source-preservation control (lower noise preserves more of the Lyria track). This fixture intentionally generates a new base for an A/B evaluation; production variants should instead pass a previously generated or stored track to `MusicAdapter`. Set `FAL_API_KEY` or `FAL_API_KEY` alongside the Lyria/Gemini key before launching it.

The Speech Provider Bench compares Gemini 3.8 Flash TTS, ByteDance **Seed Speech v2** on FAL, and Chirp 3 HD using fixed narrative dialogue lines. The Gemini presets demonstrate 3.8's structured sustained delivery directions and transcript-level vocal tags such as `<short pause>`. Gemini uses `GEMINI_API_KEY`; Seed Speech uses `FAL_API_KEY`. (Seedance is ByteDance's video family; Seed Speech is its FAL TTS endpoint.)

Use the **Voice tags / filters** field on a provider card to benchmark its automatic voice selection. Bare tags work across providers (for example, `female` or `male`); Gemini also accepts its Extended Voice Library filters in `field=value` form, such as `female, accent=General American, persona=Storyteller & Narrator`. Each result shows the selected voice ID used for synthesis.

To inspect the live Gemini Extended Voice Library and its available filter values, set `GEMINI_API_KEY` and run:

```powershell
uv run python testlab/list_gemini_voice_tags.py
```

To bypass the browser and exercise one real Vertex-backed planner turn, run:

```powershell
python testlab/story_planner_smoke.py
```

To exercise one real A2UI Canvas generation turn in an isolated temporary theater,
using a two-knight health-bar scenario, run:

```powershell
python testlab/a2ui_canvas_smoke.py
```

Add `--image testlab/images/trace-knight-sword.png` to include a canvas image in the request.

The same configurable test is available in the Test Lab server at `/a2ui-canvas`. It accepts a model,
canvas request, optional workspace-local image, expected surface count, and an optional persistence
expectation. The browser default is a general canvas prompt; the health-bar scenario is reserved for the CLI smoke test.
# Adventure Runner and Game Actions

Open `/adventure-runner`, start an adventure session, and choose the **Game** tab
to play its `game/game.yaml` (or `game.yml` / `game.json`) panel. The runner copies the package into its isolated
theater and shares the real game state with the story responder and planner.
Player moves use the same validation, revision checks, and planner callbacks as
the main canvas. Moves and any resulting narration appear in the session history.
Reset reloads the package and starts a fresh game.

The CLI autoplayer can choose either in-character text or a game control:

```powershell
uv run python testlab/adventure_runner.py --adventure example_adventure --autoplay --turns 10
```

Each decision receives the current visible game panel, including enabled buttons,
their exact arguments, and the current revision. Game moves run as the player;
the story planner retains control of Narratron actions. The runner waits for
queued planning before the next decision and does not retry committed game moves.

# Game Testing Lab

Open `/game-lab` in the Test Lab server (`uv run python -m testlab`) to choose a
workspace `game.yaml`, `game.yml`, or `game.json`, text provider, optional model override, and player scenario.
Click **Load fresh game** to play manually through the same declarative board,
buttons, selection, confirmations, and disabled controls used in canvas. Each
click updates an isolated session and triggering actions run the AI response
sequence automatically. Inspect the model state, AI explanations, and full turn
transcript beside the game, or download the session JSON. **Close session** removes
its temporary files; **Load fresh game** starts over. Loading a game snapshots its
package, so later edits to the source require loading a fresh session. Unused
sessions expire after one hour and are cleaned up on the next session creation.
AI failures appear on the game panel with the last committed state preserved.

The **Automated scenario validation** section retains repeatable scenario replay
in a separate temporary game.
The default scenario exercises selection and an opening move in the example
checkers game. Download the JSON report to inspect prompts, raw model replies,
committed AI actions with revisions, before/after state, timings, and errors.

For a command-line run:

```powershell
uv run python -m testlab.game_harness --game adventures/example_adventure/game/game.yaml --scenario testlab/scenarios/checkers.json --provider gemini-3 --output output/game-test/report.json
```

Live runs use the existing provider configuration and credentials loaded from
`.env`. The game lab and CLI default to `gemini-3.5-flash-lite`. Add `--model`
to select another model. Exit status is 0
on pass and 1 on failure. Every run loads a fresh temporary game; source packages
and theater saves are untouched.

Scenario JSON contains a `name`, sequential `steps`, and optional
`max_ai_actions` (default 8 per player step). Each step contains:

- `move`: `{ "name": "move", "arguments": { "source": 40, "target": 33 } }`.
  Action names and arguments come from the package, so other games work too.
- `min_ai_actions`: optional minimum number of committed AI actions; defaults to
  1 when a triggering player move makes Narratron actions available, otherwise 0.
- `expected_state`: optional map of full-state fields to exact expected values,
  checked after the AI response sequence. Fields omitted here are unconstrained.
- `require_quiescent`: optional boolean (default false). Require that no Narratron
  actions remain available after the response; useful for turn-based games.

The model receives the package's Narratron instructions, projected model state,
current revision, and available action contracts. It returns one structured action
and a nonempty explanation at a time. Each action goes through the production
`GameState` runtime, including actor, parameter, guard, state-schema, invariant,
and view checks. Subsequent requests receive the newly committed state. A null
action stops the response sequence; the action budget prevents infinite loops.
Nontriggering moves and moves with no available Narratron actions skip generation.
A scenario that never exercises an AI response fails rather than claiming success.
The first failure stops the scenario and records the current state.

This harness evaluates model action selection directly through text providers.
It does not exercise the live story planner's heartbeat queue or scheduling, and
legal responses alone do not establish strategic playing strength.

Run the offline harness tests with:

```powershell
uv run pytest -n 0 testlab/game_harness_test.py testlab/game_lab_test.py
```
