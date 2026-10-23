# Your theater package

Start with `theater.yaml` for persona, visual style, music, and runtime settings.
To make an interactive adventure, set `story_planning.adventure_mode: true` and
choose whether `auto_begin` should open the scene automatically.

`planning.yaml` defines persistent sticky topics. Every topic has exactly one
owner (`recorder` for established facts or `planner` for contingent developments).
Use string fields and initial values. Keep `Upcoming Encounter Suggestion` hidden
in `story_planning.hidden_stickies` as well as in its topic definition.

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

Edit and save files here, download the draft to keep a package copy, and use
Deploy & test when ready. Add `metadata.json` when preparing a catalog adventure.
