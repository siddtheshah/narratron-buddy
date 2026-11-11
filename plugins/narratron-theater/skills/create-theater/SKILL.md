---
name: create-theater
description: Create, edit, and package complete Narratron theaters and interactive adventures, including lore, configuration, planning, characters, scenes, and optional games. Use when a user wants to develop a Narratron theater in their AI app and upload the finished package later.
---

# Create a Narratron theater

Work with the user in this app until their theater is ready. Narratron is the destination for uploading and playing the finished package; no Narratron account connection is needed for authoring.

## Start or continue

- For a new theater, establish the premise, tone, player role, and opening situation. Use reasonable defaults for unspecified details and start creating actual files.
- For an existing theater, inspect the supplied files and preserve the user's established world, configuration, and media while making the requested changes.
- Read [the authoring guide](references/writing_adventures.md) before creating configuration or lore. Use [the default configuration](assets/theater_default.yaml) for supported setting names and defaults. These resources are bundled in the downloadable kit.
- For game mechanics, read [game packages](references/game_packages.md). Read [HTML panels](references/game_html_panels.md) and [the canvas interface](references/game_canvas_interface.md) only if the game needs them.

## Build the theater

Create a dedicated theater folder containing `theater.yaml`, optional `metadata.json`, `lore/`, and the media the experience actually uses. For an interactive adventure, enable `story_planning.adventure_mode`, supply a nonempty `planning.yaml`, and write separate `lore/RESPONDER.md`, `lore/PLANNER.md`, and `lore/RECORDER.md` role guides. Each role needs complete instructions of its own; shared facts belong in other lore documents. Follow the authoring guide's planning contract, field types, and ownership rules.

Use one folder per character under `references/characters/<Name>/`, with portraits and `character.yaml`; use one per scene under `references/scenes/<Name>/`, with imagery and `scene.yaml`. Other reference images go under `references/`, tokens under `stamps/`, and audio under `playlists/<Mood>/`. Keep all file references relative to the theater root and preserve matching names when renaming assets.

Use the app's available image and file tools to create requested assets. Never disguise placeholder text as image or audio files. If a requested asset cannot be generated with the available tools, explain what remains and keep configuration usable without referencing a missing file. Authoring should use the user's AI app capabilities; do not require Narratron API keys or a local Narratron server.

For games, prefer `game/game.yaml` and keep exactly one game declaration. Follow the transactional turn-based interaction model and the documented CEL syntax. Do not invent unsupported runtime settings.

## Validate and deliver

1. Parse YAML and JSON with available tools. Check configuration references, media paths, role guides, and planning contracts against the bundled documentation. If working in the Narratron repository, use its validation tools with `uv run`; a standalone workspace does not require installing Narratron.
2. Inspect the resulting file tree. The upload must contain actual authored files, no plugin files, credentials, runtime output, or machine-specific paths. Respect the importer limits: 500 files, 10 MB per file, 100 MB total.
3. Create a ZIP containing the theater files with `theater.yaml` at its root. Provide the downloadable ZIP, and keep the editable theater folder available for further iteration. If this environment cannot create downloadable files, say so explicitly and provide the file contents and paths without claiming a ZIP exists.
4. Tell the user to extract the ZIP, open https://narratron.app/theater-editor, choose **Upload a folder**, select the extracted theater folder, then **Save & test**. For an existing theater, open it and use **＋ Folder** to import edits; folder import replaces matching paths but does not delete omitted files, so remove obsolete files explicitly in the editor.
5. Summarize what is playable and any missing assets or runtime checks. File validation does not establish that the theater has been play-tested in Narratron. Uploading is the user's final step; do not publish or upload automatically.
