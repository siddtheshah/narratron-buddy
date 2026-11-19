# Submitted Avatars Directory

This directory (`avatars/`) is the repository for pre-defined, submitted 3D avatar packages in **Narratron**. Avatars in this directory can be browsed and attached directly to any theater in the Theater Builder (`/theater-editor`).

## Avatar Package Layout

Each avatar package lives in `avatars/<slug>/`:

```text
avatars/<slug>/
├── metadata.json          # Catalog card metadata (id, title, description, author, genre, tags, cover_image)
├── README.md              # Optional documentation and animation notes
├── avatar.json            # Required avatar manifest (id, title, model, poster, actions, shaders)
├── model.glb              # 3D glTF binary model
├── preview.webp           # Poster/preview image shown while loading and in the catalog
└── shaders/               # Optional custom GLSL shaders (.vert, .frag, .glsl)
    ├── dust.vert
    └── dust.frag
```

## Versioning and Publishing Avatars

The shared content command manages Git history separately from the app-served
GCS catalog:

```bash
# Snapshot local changes, commit, and push to the avatar Git remote:
uv run python scripts/content.py upload --content avatars --message "Update avatar"
# Pull versioned avatars into the local editing folder:
uv run python scripts/content.py download --content avatars
# Publish the uploaded commit to the catalog served by the app:
uv run python scripts/content.py publish --content avatars
```

Add `--package narratron` to select one package, or `--dry-run` to preview an
operation without remote access or changes. Publishing reads an uploaded Git
commit; local edits require another upload before they can be published.
Run `uv run python scripts/content.py --help` for setup and recovery instructions.

## Testing & Attaching Avatars

- **Interactive Avatar Lab**: Open `/avatar-lab` to preview avatar models, actions, and shaders.
- **Attach in Theater Editor**: Open `/theater-editor`, click **＋ Avatar**, select your avatar from the library, and click **Attach Avatar**.
