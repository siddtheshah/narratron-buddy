# Your theater package

Start with `theater.yaml` for persona, visual style, music, and runtime settings.
To make an interactive adventure, set `story_planning.adventure_mode: true` and
choose whether `auto_begin` should open the scene automatically.

`planning.yaml` defines persistent sticky topics. Every topic has exactly one
owner (`recorder` for established facts or `planner` for contingent developments).
Each `fields` entry contains string `description` and `initial` values.
Omit `render` to display `field: value | field: value`, or provide a custom template. Set `hidden: true` on
`Upcoming Encounter Suggestion` in `planning.yaml`. Every topic is required and
pinned in contract order. With `story_planning.enforce_structured: true` (the
default), Adventure Mode requires a nonempty `planning.yaml`.
Set it to `false` to let recorder and planner create and update shared freeform
topics without a contract. Declared topics retain ownership and field validation.
Freeform notes are visible by default and older optional notes can be evicted.

The `notepad` section configures only normal narration: `initial_stickies`,
`max_stickies`, `required_stickies`, and `hidden_stickies`. Adventure Mode
ignores those settings and reads its stickies exclusively from `planning.yaml`.

The three pinned role guides in `lore/` are independent: `RESPONDER.md` owns
narration, `RECORDER.md` owns factual reconciliation and validated game actions,
and `PLANNER.md` owns future plans. Put shared canon and mechanics in separate
lore files, then add their paths to each guide's roadmap.

Character references belong in `references/characters/<Name>/` with
`character.yaml` and numbered portraits such as `1.webp`. Scene references belong
in `references/scenes/<Name>/` with `scene.yaml` and numbered images. Use canonical
names in lore. Put cover art in `references/`, canvas tokens in `stamps/`, and
tracks in `playlists/<Mood>/` with an optional `description.txt`.

For a structured game, add exactly one declaration: `game/game.yaml` (preferred),
`game/game.yml`, or `game/game.json`. Optional SVG assets and companion JSON schema
or data files also live under `game/`; keep the runtime's `state_schema` inline in
the declaration.
Games need turn limits or explicit acceptance at shared transaction boundaries.
The game owns its state; sticky notes should not duplicate it. Outside Adventure
Mode, live-agent play additionally requires `game_tool.enabled: true`.

Editor and canvas use the same working theater. Use Save theater to keep your
changes, download the theater to keep a package copy, or Save & test to open the
canvas. Unsaved changes can be lost if the working copy is cleared.
Add `metadata.json` when preparing a catalog adventure.
