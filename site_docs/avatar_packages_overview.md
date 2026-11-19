# Theater avatar packages

Avatar packages add a 3D animated companion to the bottom-right corner of a
Narratron canvas. Each theater or adventure can bring its own avatar—such as
Narratron's floating storyteller, the Cube of Light, or a custom character—that
reacts to narration and viewer interactions.

## The basic concepts

- **Model** is a self-contained 3D glTF binary with embedded materials, textures, and named animation clips.
- **Actions** are the poses, gestures, and mood lighting colors the avatar can play—such as idling, greeting, looking surprised, or shifting its glow.
- **Reactions and glow** let the avatar brighten on hover or focus and respond locally when a viewer hovers over or clicks it.
- **Shaders** are optional custom WebGL visual effects, such as volumetric nebula dust or luminous auras, that react to time, hover brightness, active color, and action pulses.
- **Preview poster** is a static fallback image shown in the catalog and whenever 3D rendering is unavailable.

## How avatars behave during a session

When an avatar is enabled, Narratron automatically selects an action that fits
the tone and emotion of each thought or chat message it sends. Every connected
viewer sees the avatar transition smoothly into that action and mood color, then
return to its idle animation after one-shot gestures finish.

Viewers can also hover over or click the avatar on the canvas to trigger its
configured local reactions without interrupting the rest of the room.

Avatars respect browser and canvas accessibility preferences:

- **Minimal Mode** skips custom shaders so the base 3D model stays lightweight.
- **Reduced Motion** pauses continuous animation and shader time while keeping pose and color updates instant and readable.
- **Poster fallback** displays the preview image automatically if WebGL 2 is unavailable or resets.

## Adding or creating an avatar

To use an existing avatar, open the Theater Editor, choose **＋ Avatar**, pick
an avatar from the library, and click **Attach Avatar**. This copies the avatar
files into your theater and enables it in your theater configuration.

To build your own avatar, start with a self-contained 3D model framed for a
square orthographic viewport, define its actions and clear mood descriptions in
its manifest, and preview it in the Avatar Lab.

For authoring details, manifest fields, shader uniforms, and examples, see the
[full avatar package guide on Narratron's GitHub](https://github.com/siddtheshah/narratron-buddy/blob/main/site_docs/avatar_packages.md).
