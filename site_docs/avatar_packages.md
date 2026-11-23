# Theater avatar packages

`avatars/<package>/avatar.json` declares a 3D companion avatar's model, poster,
playable actions, hover glow, pointer reactions, and optional custom GLSL
shaders. Narratron loads avatar packages generically without avatar-specific
renderer code. Bundled references include:

- `example_theater/avatars/example_avatar`: the default example theater's
  smiley face, with a looping smile and laugh, surprise, and thinking reactions.
  Preview it in Avatar Lab with `?avatar=example_avatar` or rebuild its model
  with `node scripts/build_smiley_avatar.mjs`. The avatar content uploader skips
  this package.
- `example_theater/avatars/narratron`: rigged storyteller with six animation
  clips, hover glow, and two volumetric GLSL shaders (`dust.frag` and
  `spark.frag`).
- `adventures/example_adventure/avatars/light-cube`: luminous floating cube
  using a single looping clip, six color-only mood actions, and a dust shader.
- `testlab/assets/avatar_packages/cube`: minimal reaction cube demonstrating `hover`
  and `activate` reactions without custom shaders.

Avatar packages are mounted by adventure selection, folder import, ZIP import,
and the Theater Editor's **＋ Avatar** library picker.

## Package layout and allowed files

Place custom theater avatars under `avatars/<package>/` at the theater root:

```text
avatars/<package>/
├── avatar.json        # Required manifest
├── model.glb          # Required self-contained 3D glTF binary model
├── preview.webp       # Optional (recommended) static poster fallback
└── shaders/           # Optional custom GLSL shaders (.vert, .frag, .glsl)
    ├── dust.vert
    └── dust.frag
```

Package rules enforced by `components/theater_package.py` and
`components/canvas/avatar_state.py`:

- **Package ID (`<package>`)**: must match `^[a-z0-9][a-z0-9_-]*$`
  (case-insensitive; starts with an alphanumeric character and contains only
  letters, digits, underscores, or hyphens).
- **Allowed asset files**:
  - `avatars/<package>/avatar.json`
  - `avatars/<package>/model.glb`
  - `avatars/<package>/preview.webp`
  - Custom shaders ending in `.glsl`, `.vert`, or `.frag` placed either directly
    under `avatars/<package>/` or one level deep under
    `avatars/<package>/shaders/`. Deeper subdirectories and other file types are
    rejected.
- **Theater Editor limits**: 10 MiB per file, 100 MiB total draft size, 500
  files total, and 128 KiB per text edit. Keep `model.glb` compact so viewers
  load the canvas quickly.

For reusable catalog packages submitted under the repository's top-level
`avatars/<slug>/` directory, also include a `metadata.json` card descriptor (see
[Publishing to the avatar catalog](#publishing-to-the-avatar-catalog)).

## Enabling an avatar in `theater.yaml`

Add an `avatar` section to `theater.yaml`:

```yaml
avatar:
  enabled: true
  package: light-cube
  # Optional: override the decision provider used to select avatar actions.
  # Defaults to story_planning.decision_provider (or "openai-decisions").
  # decision_provider: openai-decisions
```

- `enabled` (`boolean`, default `false`): mounts the avatar on the canvas and
  enables server-side action selection when `avatars/<package>/avatar.json`
  exists and is valid.
- `package` (`string`): folder name under `avatars/` matching
  `^[a-z0-9][a-z0-9_-]*$`.
- `decision_provider` (`string`, optional): provider ID used to pick an avatar
  action when Narratron sends a thought/chat message. If omitted, reuses
  `story_planning.decision_provider` (default `"openai-decisions"`).

## Minimal package

A minimal `avatars/lantern/avatar.json` looks like this:

```json
{
  "id": "lantern",
  "title": "Guiding Lantern",
  "model": "model.glb",
  "poster": "preview.webp",
  "idle": "idle",
  "reactions": {
    "hover": "perk",
    "activate": "flare"
  },
  "actions": [
    {
      "id": "idle",
      "label": "Idle",
      "clip": "Float",
      "loop": true,
      "lightColor": "#ffd27d",
      "description": "Calm, steady floating flame."
    },
    {
      "id": "perk",
      "label": "Perk Up",
      "clip": "Tilt",
      "loop": false,
      "lightColor": "#7ce8ff",
      "description": "Curious lean forward when paying attention."
    },
    {
      "id": "flare",
      "label": "Flare",
      "clip": "Pulse",
      "loop": false,
      "lightColor": "#ff6b4a",
      "description": "Bright burst of flame for excitement or warning."
    }
  ],
  "renderScale": 1.0,
  "fillLight": 0.6,
  "hoverGlow": {
    "material": "FlameCore",
    "intensity": 3.5,
    "light": { "name": "FlameLight", "intensity": 6.0 }
  }
}
```

## Manifest reference (`avatar.json`)

### Top-level fields

- `id` (`string`, optional on canvas, used by catalog): canonical package slug.
- `title` (`string`, required): accessible label (`aria-label`) on the canvas
  avatar mount and display title passed to the decision provider.
- `model` (`string`, required): must be `"model.glb"`. Any other value is
  rejected by the canvas avatar loader.
- `poster` (`string`, optional): must be `"preview.webp"` when set. Displayed as
  a fallback `<img>` if WebGL 2 is unavailable, the model fails to load, or the
  WebGL context is lost. If omitted and WebGL fails, the mount remains hidden.
- `idle` (`string`, required): `id` of the resting action in `actions`. Plays
  automatically on load; one-shot actions automatically crossfade back to `idle`
  when their clip finishes.
- `reactions` (`object`, optional): local viewer interaction triggers:
  - `hover`: action `id` played on non-touch `pointerenter` while the avatar is
    currently in its `idle` action (disabled under `prefers-reduced-motion`).
  - `activate`: action `id` played on click/tap or `Enter`/`Space` when the
    avatar is focused. Clicking the avatar stops pointer propagation so canvas
    interactions underneath are not triggered.
- `actions` (`array`, required): ordered list of playable actions (deduplicated
  by trimmed `id` on the server; up to 255 actions are considered by the
  decision provider).
- `renderScale` (`number`, optional, default `1`): scales the avatar container's
  width via `--canvas-avatar-scale`, clamped to `0.5`–`1.5`. The base widget is
  a `1:1` square sized `clamp(6rem, 11vw, 9rem)` anchored bottom-right above the
  canvas corner controls (`right: 1.5rem; bottom: calc(1.85rem + 44px)`).
- `fillLight` (`number`, optional, default `1`): multiplier applied to the host
  scene's ambient and directional lights, clamped to `0`–`1`. Lower values (such
  as `0.24` in `light-cube` or `0.6` in `narratron`) let internal point lights,
  emissive materials, and shaders stand out cleanly.
- `hoverGlow` (`object`, optional): brightens an emissive material and an
  optional embedded `PointLight` on hover or keyboard focus:
  - `material` (`string`, required): exact `MeshStandardMaterial.name` in
    `model.glb`. Its `emissiveIntensity` lerps between its resting GLB value and
    `intensity`, and its `emissive` color tracks the active `lightColor`.
  - `intensity` (`number`, required): peak `emissiveIntensity` on hover/focus.
  - `light` (`object`, optional): `{ "name": "<PointLight name>", "intensity": <peak> }`.
    Targets a `PointLight` node embedded in `model.glb`, lerping its intensity
    from its resting GLB value to `intensity` and tinting it with `lightColor`.
- `shaders` (`array`, optional): list of custom WebGL 2 shader entries (see
  [Custom GLSL shaders](#custom-glsl-shaders)).

### Action entries (`actions[]`)

- `id` (`string`, required): unique action identifier referenced by `idle`,
  `reactions`, and server action updates.
- `label` (`string`, optional): display name shown in Avatar Lab and sent to the
  decision provider (defaults to `id`).
- `description` (`string`, optional): concise summary of the gesture, mood, or
  intent. This text is shown directly to the decision provider when Narratron
  chooses an action for a message, so write clear, non-overlapping descriptions.
- `clip` (`string`, optional): exact `name` of an `AnimationClip` in
  `model.glb`. Omit `clip` to create color-only mood actions that tint lights,
  emissives, and shaders without switching the running animation clip (as
  `light-cube` does).
- `loop` (`boolean`, optional, default `false`):
  - `true`: loops continuously (`THREE.LoopRepeat`).
  - `false`: plays once (`THREE.LoopOnce`, clamped on the last frame) and
    automatically crossfades back to `idle` when finished.
- `lightColor` (`string`, optional): 6-digit hex color (`#rrggbb`). Smoothly
  lerps `hoverGlow.light`, `hoverGlow.material.emissive`, and shader color
  uniforms when the avatar has a `hoverGlow.light` or at least one shader. If a
  non-idle action omits `lightColor`, returning to `idle` restores `idle`'s
  `lightColor`.

## 3D model (`model.glb`) and scene contract

### Embedded resources only

`frontend/shared/avatar-scene.ts` loads `model.glb` with a Three.js
`LoadingManager` that rejects external URLs; only `blob:` and `data:` URIs are
permitted. Pack all textures, geometry buffers, and animations directly inside
the binary `.glb` file.

### Camera, framing, and lighting rig

Every avatar renders inside a transparent square WebGL 2 canvas using
`ACESFilmicToneMapping` (pixel ratio capped at `2`) and a fixed scene setup:

- **Camera**: `OrthographicCamera(-1.16, 1.16, 1.16, -1.16, 0.1, 30)` positioned
  at `(2.4, 1.9, 5.6)` looking at `(0, 0.28, 0)` (slightly elevated three-quarter
  front view).
- **Framing**: center the avatar around `x = 0, z = 0` and vertically around
  `y ≈ 0.28`. Keep the full animated silhouette—including bobbing, arm gestures,
  and attached shader halos—comfortably inside roughly `[-1.0, 1.0]` in view
  space so edges never clip against the square widget bounds.
- **Host lights** (all scaled by `fillLight`):
  - `HemisphereLight(0xe8ffff, 0x7e6470, 2.1 * fillLight)`
  - Key `DirectionalLight(0xffdec0, 2.4 * fillLight)` at `(-3, 5, 5)`
  - Rim `DirectionalLight(0x76f5ff, 1.4 * fillLight)` at `(4, 2, -3)`
- **Embedded lights**: models can include a `PointLight` exported via the glTF
  `KHR_lights_punctual` extension and reference its node name in
  `hoverGlow.light.name`.

### Animations and named nodes

- Give every animation clip a clear name matching `actions[].clip`.
- Transitions crossfade over `0.25 s` (or snap immediately when
  `prefers-reduced-motion: reduce` is enabled). Design one-shot clips so their
  start and end poses blend smoothly with the `idle` clip.
- Name `MeshStandardMaterial` materials explicitly when targeting them with
  `hoverGlow.material` or `shaders[].replaceMaterial`.
- Name anchor `Object3D`/`Group`/`PointLight` nodes explicitly when attaching
  shaders via `shaders[].attachTo` (for example, `NebulaAnchor` or `SparkLight`
  in `example_theater/avatars/narratron`).

### Authoring and exporting `.glb`

You can build `model.glb` either in a 3D editor or procedurally:

- **Blender**: model facing `+Z` (front slightly rotated toward the `(2.4, 1.9, 5.6)`
  camera), keep scale applied, author clips as separate Actions / NLA strips,
  and export as **glTF Binary (.glb)** with **Punctual Lights** enabled if using
  an embedded `PointLight`.
- **Procedural Three.js scripts**: assemble meshes, lights, and
  `THREE.AnimationClip` tracks in Node and export via `GLTFExporter`. See
  `scripts/build_light_cube.mjs` and `scripts/build_narratron_avatar.mjs` for
  complete working examples.

## Custom GLSL shaders

Optional `shaders` entries in `avatar.json` compile as `THREE.ShaderMaterial`
instances at runtime (`frontend/canvas/avatar-shaders.ts`).

### Shader paths and modes

Each shader entry specifies relative `vertex` and `fragment` paths ending in
`.vert`, `.frag`, or `.glsl` inside the avatar package (no leading `/`, no `..`,
and no paths outside the package root).

A shader entry operates in one of two modes:

1. **Attached geometry overlay** (default when `replaceMaterial` is omitted):
   - `attachTo` (`string`, optional): name of an `Object3D` in `model.glb`
     (`model.getObjectByName`). Defaults to the model root if omitted; fails
     shader loading if a specified name is not found.
   - `geometry` (`object`, optional; defaults to a unit sphere):
     - `{ "type": "sphere", "radius": 1, "widthSegments": 32, "heightSegments": 24 }`
       (`radius >= 0.01`, segments clamped `3`–`64`).
     - `{ "type": "box", "width": 1, "height": 1, "depth": 1 }` (each `>= 0.01`).
     - `{ "type": "plane", "width": 1, "height": 1 }` (each `>= 0.01`).
   - `scale` (`number` or `[x, y, z]`, optional): mesh scale.
   - `position` (`[x, y, z]`, optional): local offset relative to `attachTo`.
2. **Material replacement**:
   - `replaceMaterial` (`string`): replaces the material on every `Mesh` in
     `model.glb` whose single material matches `material.name === replaceMaterial`.
     When shaders are disabled (such as in Minimal Mode) or disposed, the
     original GLB material is restored automatically.

### Material options

- `transparent` (`boolean`, default `false`)
- `depthWrite` (`boolean`, default `true`)
- `blending`: `"normal"` (default) or `"additive"`
- `side`: `"front"` (default), `"back"`, or `"double"`

### Uniforms

Every shader automatically receives five built-in uniforms:

- `uTime` (`float`): elapsed time in seconds (paused under reduced motion).
- `uBrightness` (`float`): `0.0` at rest to `1.0` on hover/focus (smoothly
  damped).
- `uActionPulse` (`float`): jumps to `1.0` whenever a non-idle action triggers
  and decays linearly back to `0.0` over `1.2 s` (`0.0` under reduced motion).
- `uColor` (`vec3`): active `lightColor` (smoothly lerped between actions).
- `uResolution` (`vec2`): current WebGL canvas pixel dimensions `(width, height)`.

You can also declare custom entries in `uniforms`:

- **Static value**: finite number (`float`), `"#rrggbb"` hex string (`vec3`), or
  a 2-, 3-, or 4-element number array (`vec2`, `vec3`, `vec4`).
- **Hover range**: `{ "rest": 0.72, "hover": 1.55 }` — linearly interpolates
  between `rest` and `hover` using `uBrightness`.
- **Dynamic source alias**: `{ "source": "time" | "brightness" | "lightColor" | "resolution" | "actionPulse" }` — binds a built-in stream under your chosen uniform name.

Example shader entry from `example_theater/avatars/narratron/avatar.json`:

```json
{
  "attachTo": "NebulaAnchor",
  "geometry": { "type": "sphere", "widthSegments": 32, "heightSegments": 24 },
  "scale": 0.92,
  "vertex": "shaders/dust.vert",
  "fragment": "shaders/dust.frag",
  "transparent": true,
  "depthWrite": false,
  "uniforms": {
    "sparkColor": { "source": "lightColor" },
    "sparkStrength": { "rest": 0.72, "hover": 1.55 }
  }
}
```

### Orthographic shader tips

- Because the avatar camera is orthographic, every view ray travels along
  view-space `-Z`. In a vertex shader, you can compute the object-space
  direction toward the camera as:

```glsl
viewDirection = normalize(vec3(
  modelViewMatrix[0].z,
  modelViewMatrix[1].z,
  modelViewMatrix[2].z
));
```

- End fragment shaders with Three.js's built-in chunks so shader colors match
  the renderer's tone mapping and sRGB output:

```glsl
#include <tonemapping_fragment>
#include <colorspace_fragment>
```

See `example_theater/avatars/narratron/shaders/dust.vert`, `dust.frag`, and
`spark.frag` for complete volumetric GLSL examples.

## Live-agent integration and canvas runtime

### Automatic action selection

Whenever the live agent updates Narratron's thought/chat message via
`send_chat_message(text)` (`tools/chat_tool.py`), Narratron automatically picks
an avatar action when `avatar.enabled` is true:

- If the manifest defines a single action, that action is applied directly.
- If the manifest defines 2 or more actions (up to 255), the configured
  `DecisionProvider` receives the message text, the avatar `title`, and each
  action's `id`, `label`, default-idle marker, `lightColor`, and `description`,
  and returns one action `id`.
- `AvatarState.set_action` validates the ID against the loaded manifest,
  increments a monotonic `sequence`, and broadcasts the transient state in
  `/api/latest` under `avatar`:

```json
{
  "enabled": true,
  "package": "narratron",
  "action": "greeting",
  "sequence": 4,
  "time": 1763512345.12
}
```

All connected viewers watch `avatar.sequence` and call `playAction(action)` (or
queue the latest action until `model.glb` finishes loading). Write distinct,
expressive action `description` strings so the decision provider reliably
distinguishes between moods (for example, `amused` vs `surprised` vs
`confused`).

### Minimal Mode, Reduced Motion, and fallbacks

- **Minimal Mode**: When a viewer enables Minimal Mode in the canvas left menu
  (`minimal-mode` on `<html>`), the loader skips fetching custom shaders on
  startup and disables/hides shaders if toggled on mid-session (restoring
  original materials for `replaceMaterial` entries). Always make sure your base
  `model.glb` looks complete even without custom shaders.
- **Reduced Motion**: When `prefers-reduced-motion: reduce` is active,
  continuous clip advancement, shader `uTime`, `uActionPulse`, and hover-based
  action triggers pause; explicit actions and color selections still apply
  immediately with a `0 s` crossfade.
- **WebGL fallback**: If WebGL 2 is unavailable, `model.glb` fails to load, or a
  `webglcontextlost` event occurs, the canvas swaps to `preview.webp` (when
  `"poster": "preview.webp"` is declared). Always include a clean, transparent
  or well-framed `preview.webp`.

### Security and delivery

Avatar packages never execute theater JavaScript or inject HTML/CSS. Assets are
served from `/theaters/{theater_id}/avatars/{avatar_id}/{filename:path}` behind
normal theater canvas access checks, validated by `safe_asset_path`, and
delivered with `X-Content-Type-Options: nosniff`.

## Testing, previewing, and publishing

### Preview in Avatar Lab

Start the local Test Lab server and open `/avatar-lab`:

```powershell
uv run python -m uvicorn testlab.server:app --host 127.0.0.1 --port 8015
```

Visit `http://127.0.0.1:8015/avatar-lab` (or `?avatar=narratron` /
`?avatar=light-cube`) to inspect framing over Story scene, Dark, and Light
backdrops, click through every action and mood color, and verify hover/activate
reactions and reduced-motion behavior.

### Test inside a theater

1. Place your files in `avatars/<package>/` and add the `avatar:` block to
   `theater.yaml` (or upload a theater folder in `/theater-editor`, or attach a
   catalog avatar via **＋ Avatar**).
2. Click **Save & test →** to launch the canvas and verify:
   - Initial idle clip, framing, and `renderScale` above the bottom-right corner
     controls.
   - Hover glow, click/keyboard `activate` reaction, and Minimal Mode toggle.
   - Live-agent action changes when Narratron posts thought/chat messages.
   - Poster fallback (`preview.webp`) if WebGL is disabled.

### Run automated checks

Run the backend avatar and Playwright browser test suites:

```powershell
uv run pytest -n 0 components/canvas/avatar_state_test.py services/avatar_service_test.py tools/chat_tool_test.py
npm run build
npm run test:ui -- frontend/canvas/avatar_test.ts
```

### Publishing to the avatar catalog

To add a reusable avatar to the Theater Editor's **＋ Avatar** library, place
the package under `avatars/<slug>/` with a `metadata.json` file alongside
`avatar.json`, `model.glb`, `preview.webp`, and any `shaders/`:

```json
{
  "id": "lantern",
  "title": "Guiding Lantern",
  "description": "Floating brass lantern with warm flickering aura and mood color presets.",
  "author": "Your Name",
  "genre": "3D Avatar",
  "tags": ["Avatar", "3D", "Fantasy", "Lantern"],
  "created_at": "2026-11-19T00:00:00Z",
  "cover_image": "preview.webp"
}
```

Then version and publish with the shared content script (see `avatars/README.md`
for details):

```powershell
uv run python scripts/content.py upload --content avatars --package lantern --message "Add lantern avatar"
uv run python scripts/content.py publish --content avatars --package lantern
```
