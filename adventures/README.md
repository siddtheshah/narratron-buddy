# Narratron Premade Adventures

This directory contains premade narrative adventures for Narratron Buddy.

Use the shared content command to version and publish adventures:

```bash
uv run python scripts/content.py upload --content adventures --message "Revise lore"
uv run python scripts/content.py download --content adventures
uv run python scripts/content.py publish --content adventures
```

Upload snapshots, commits, and pushes to the adventure Git remote. Publish
deploys an uploaded commit to the app-served GCS catalog. Add `--package <slug>`
to select one adventure or `--dry-run` to preview. Setup and recovery instructions
are embedded in `uv run python scripts/content.py --help`.

- For the full guide on authoring, testing, and submitting adventures, see:
  👉 **[Writing Adventures Documentation](../site_docs/writing_adventures.md)**

- To explore a functional reference template showing the required folder structure and configuration:
  👉 **[Example Adventure Package](example_adventure/)**

## Submitting Your Adventure

You are free to package and distribute your adventures as folders or zip archives anywhere you like.

To have your adventure reviewed and featured on **[narratron.app](https://narratron.app)**, ping **`syclonex`** on **Discord** with your package or repository link!
