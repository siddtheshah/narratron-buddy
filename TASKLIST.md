# Narratron Task List


## Auth, Security & Monetization
- [ ] Add rate limits for registration/login, password reset, join-key resolution, uploads, payment attempts, and Live WebSocket connections.
- [ ] Use secure production auth cookies and CSRF protection for authenticated state-changing endpoints.
- [ ] Require authenticated theater ownership (or the active baton holder where appropriate) for agent start, stop, and status endpoints.
  - The current lifecycle endpoints accept a theater ID without checking the caller's identity or authorization.
- [ ] Cloudflare

## Walkable 3D Adventure Prototype

Build a small Blender-authored adventure to validate exploration and dynamic
objects through the game-to-canvas API. Package reusable assets ahead of play;
use browser rendering with Three.js and keep authoritative game state on the
server. Live asset generation and server-rendered video are later options.

- [x] Create a reproducible Blender scene and export workflow.
  - Locate or install Blender; keep the editable `.blend` source and an optional build script alongside the prototype assets. Run project Python tooling through `uv`.
  - Build two connected rooms using simple geometry and plain materials: a table, collectible key, chest, and separate animated door.
  - Export GLB assets with stable entity IDs, named spawn points, interaction targets, collision shapes, and trigger volumes. Keep objects that move or disappear separate from the environment.
  - Define units, origin, axis directions, and export transforms; verify consistent scale and orientation in the browser.
  - Target an initial scene download under 5 MB; measure the actual export size. No paid generation is required for this prototype.
  - Completed in `adventures/example_adventure/worlds/library_of_origins/`: editable source, `uv` build launcher, complete and reusable GLBs, spatial manifest, Blender renders, and Three.js preview. Scene approximately 2.05 MB; Chromium verification covers scale/orientation, door animation, and key visibility. See the world README for commands and prototype limitations.
- [ ] Extend the theater asset catalog and adventure packaging for worlds and reusable entity definitions.
  - Pin world/model revisions and their spatial metadata together; validate references before play and preserve assets through import, export, and save/load.
  - Define model, collider, animation, and interaction metadata separately; importing a model does not automatically give it game behavior.
  - Preload the current room's assets, cache reusable models, and release unused renderer resources.
- [ ] Add a dedicated 3D canvas view with walking, looking, target selection, and object animation.
  - Support adding, moving, and removing independent model instances at runtime.
  - Start with one shared player controlled by the active orator; keep spectator cameras private and prevent spectator movement from advancing the game.
  - Handle keyboard/pointer focus, text-input exclusion, Escape, blur, and reconnects without leaving movement controls held.
- [ ] Design a backward-compatible canvas API v2 spatial contract.
  - Expose world instance/asset revision, player position and body orientation, separate camera orientation, current region IDs, interaction target, and accepted observation sequence.
  - Use an explicit world coordinate system, preferably integer millimetres; retain the existing 2D image-coordinate contract for current games.
  - Add stable entity IDs and bounded observations of entity transforms and visible interaction state. Keep inventory, puzzle rules, and progression authoritative in game state.
  - Define proposed effects such as `show_world`, `spawn_entity`, `update_entity`, `remove_entity`, and `play_entity_animation`, including passage/collision changes and explicit player repositioning.
- [ ] Implement spatial input ingestion and binding to game transitions.
  - Add semantic events such as `region_entered`, `region_exited`, `target_activated`, and `movement_completed`, with declarative mappings to named game transitions.
  - Record authenticated input ownership, unique input/event IDs, world identity, and the accepted pose snapshot used for each interaction; reject forged or stale evidence and deduplicate retries.
  - Validate movement boundaries, blocked routes, and interaction reach on the host. Detect region crossings along movement paths so brief crossings are not lost between updates.
  - Keep smooth browser movement and bounded spatial updates separate from persisted game actions. Define spatial sequencing separately from game/canvas revisions so camera updates do not continuously invalidate interactions.
  - Use ordinary validated transitions for meaningful actions; use the existing `triggering` flag to narrate discoveries and puzzle changes without narrating every movement update.
- [ ] Extend shared game/canvas commits, persistence, and recovery for spatial state and effects.
  - Stage world/entity changes with game state, roll back rejected actions, and restore the world instance, accepted player pose, entity states, and consumed event identities on save/load and reconnect.
  - Define pose checkpoint frequency separately from per-frame rendering and game commits; preserve atomicity for important game changes.
  - Maintain Python/Go CEL evaluation parity, capability checks, and observation/effect resource bounds.
- [ ] Update the game-to-canvas design document to distinguish implemented behavior from planned behavior.
  - The live host already supports still-image observations and atomic game/stamp changes; it currently applies only `put_stamp` and `remove_stamp` output commands.
  - The live observation adapter currently returns `event: null`; event schemas and evaluator support alone do not provide an input pipeline. Keyboard shortcuts also need live host/frontend wiring.
- [ ] Validate the complete prototype flow: walk into a trigger area, discover the key, collect it, spawn or reveal another object, unlock and animate the door, and enter the second room.
  - Verify proximity/permission checks, blocked movement, duplicate and stale inputs, region crossings, persistence failure rollback, save/load, reconnects, and private spectator cameras.
  - Measure initial load, asset bytes, memory use, and frame rate on a representative laptop and phone with dynamic objects present; use these results to evaluate future streaming or server rendering.

## Quality Testing
- [ ] Test Deepseek V4.1-flash as an alternative to gemini.
   - Can use LiteLLM as a wrapper to get compatibility with ADK agents in story modules.

### Other refactors

- [ ] Eliminate traces of 'named element' terminology.
- [ ] Reduce direct image file os path checking. Make better interfaces to access image content within a theater.

