---
name: developing-avatars
description: Create, edit, inspect, and preview Narratron 3D theater avatar packages, including GLB models, animation actions, posters, reactions, and GLSL shaders.
---

# Develop Narratron avatars

## Choose a package and reference

- Work in `avatars/<slug>/` for a reusable catalog package, or `<theater-or-adventure>/avatars/<slug>/` for an embedded avatar. Include `avatar.json`, a self-contained `model.glb`, and a `preview.webp` poster when possible. Add `metadata.json` for a reusable catalog entry. Keep the folder, manifest, and catalog IDs aligned.
- Use the [avatar package contract](../../site_docs/avatar_packages.md) for manifest fields, the orthographic camera and lighting, shader bindings, and allowed files. Start with the [reaction cube](../../testlab/avatar_packages/cube/avatar.json) for a small interactive model, the [Cube of Light](../../adventures/example_adventure/avatars/light-cube/avatar.json) for mood colors and a shader, or [Narratron](../../example_theater/avatars/narratron/avatar.json) for multiple clips and effects.
- For procedural modeling, [the cube builder](../../testlab/build_cube_avatar.mjs) is a small Three.js GLBExporter reference. Adapt it to write into the target package. The bundled builder writes to the reference cube's own `model.glb`. Keep authoring sources with the appropriate project files and ship the exported assets allowed by the theater package.

## Build and inspect

Start with the silhouette and idle at canvas-corner size, then add actions and effects. Export a GLB with embedded resources. Check that action clip names match the exported animations and that named shader meshes, glow materials, and light targets exist in the export. Keep action IDs stable when modifying an existing package. Give actions distinct, visible cues and clear descriptions if Narratron will select them. Use the poster to convey the same silhouette during loading and on devices without WebGL.

From the repo root, install dependencies once per checkout with `uv sync --locked` and `npm ci`. For an existing lab fixture, run:

```powershell
npm run build
uv run python -m uvicorn testlab.server:app --host 127.0.0.1 --port 8015
```

Open [Avatar Lab](http://127.0.0.1:8015/avatar-lab) and select the fixture, or use [Narratron](http://127.0.0.1:8015/avatar-lab?avatar=narratron) or [Cube of Light](http://127.0.0.1:8015/avatar-lab?avatar=light-cube) directly. Try each action or mood color over the Story scene, Dark, and Light backdrops. Reload after asset changes. The lab menu lists fixed fixtures, so use the local theater workflow for a new package.

## Preview any package in a local theater

1. Run `npm run build` and `uv run main.py --testing_use_local` from the repo root. Open [Theater Builder](http://localhost:8000/theater-editor).
2. For a reusable avatar under the repo's top-level `avatars/`, start a disposable draft with **Populate default**. Click **＋ Avatar**, pick the package, leave **Enable this avatar in theater** selected, and click **Attach Avatar**. Reattach after changing the source, since the editor copies the assets. For an embedded avatar, start the draft with **Upload a folder** and verify that the imported `theater.yaml` contains `avatar: {enabled: true, package: <slug>}`.
3. Click **Save & test →** and summon Narratron to make the avatar visible in the canvas corner. Check idle, hover and activate when configured, return to idle after a one-shot, shader appearance, and poster fallback. Check a narrow viewport or mobile Watch, the Minimal Mode toggle, and reduced motion as applicable. For live-agent action selection, observe an action following a matching Narratron thought or chat message.

### Preview a new package without a live agent

With Test Lab running, reuse its packaged-avatar action picker and real renderer. Install Chromium with `npx playwright install chromium` if it is missing. In a Node Playwright session with a fresh `page`, swap the fixture's assets for a package in your checkout:

```javascript
const path = require('node:path');
const packageDir = path.resolve('avatars/<slug>');
const assetPrefix = '/avatar-assets/narratron/';
await page.route('**/avatar-assets/narratron/**', (route) => {
  const asset = new URL(route.request().url()).pathname.slice(assetPrefix.length);
  const file = path.resolve(packageDir, asset);
  if (!file.startsWith(packageDir + path.sep)) return route.abort();
  return route.fulfill({ path: file });
});
await page.goto('http://127.0.0.1:8015/avatar-lab?avatar=narratron');
```

The action buttons come from your manifest. Try a loop and a one-shot where present, and confirm that a canvas renders before capturing evidence. Close the preview context after the test. Use the theater flow to verify actual attachment and live-agent behavior.

If the avatar stays on the poster, inspect the browser console and network panel for missing assets, invalid manifest fields, shader errors, and WebGL availability. If no avatar appears, check the deployed theater's `avatar.enabled` and `avatar.package`, then check that the model and manifest were included under the selected package.

## Leave an inspectable handoff

- For runtime or attachment changes, use focused regression checks:

  ```powershell
  uv run pytest -n 0 components/canvas/avatar_state_test.py services/avatar_service_test.py api_server/theater_editor_test.py
  npm run test:ui -- frontend/canvas/avatar_test.ts frontend/remote/remote-avatar_test.ts
  ```

- Open the actual avatar in a browser before handing it over. Capture a screenshot at canvas size and, for an animation change, a short recording or clear replay steps. Share the package and any generator path, the exact preview URL or attach steps, and the actions and fallbacks verified. Say whether you saw live 3D playback or the poster.
- Keep local authoring and preview independent of [catalog publishing](../../avatars/README.md#versioning-and-publishing-avatars). New packages under `avatars/` are ignored by this app repo by default. Check `git status --ignored --short -- avatars/<slug>` if the task includes handing over a Git change.
