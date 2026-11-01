# CEL canvas interface, version 1

Status: proposed contract, partially implemented. Package validation, strict
observation/command models, and pure Python/Go CEL evaluation are implemented.
The still-image observation adapter, pinned image catalog, canonical object
geometry, and atomic local saves are also implemented. The shared commit host,
animation adapter, input integration, and remaining acceptance gates are unfinished;
canvas-enabled actions are not enabled in the live game host.
See [implementation and review notes](game_canvas_implementation.md). Existing
version-2 games continue to work without opting in.

Every game uses the same host-provided `canvas` value. It describes the shared
canvas, while `state` remains the game's authoritative inventory, mechanics,
location, and progress. A visible stamp is an observation, not proof of ownership
or an accepted move. The game panel and the main canvas submit actions through
the same permission checks, guards, invariants, and commit boundary.

The interface supports both reading and writing the canvas. CEL reads an
immutable observation and returns write commands from a transition. Those
commands can push a scene or animation, add or update real canvas stamps, and
remove stamps managed by the game. A dedicated game-overlay layer adds symbols,
text, highlights, and visual filters. These writes commit with the game action
and remain visible with the game panel closed. The game panel remains the primary
entry point for starting play, choosing actions, and inspecting game state.
Optional keyboard bindings provide shortcuts to those same game actions,
including discrete movement with the arrow keys.

## Package declaration

```yaml
canvas_api:
  version: 1
  effects: [show_scene, show_animation, put_stamp, remove_stamp, set_stamp_tray, set_overlay]
```

The declaration opts into the binding and lists permitted output operations.
An empty `effects` list enables observation only. Unsupported interface versions
or operations fail package validation. Existing packages without this declaration
retain their current behavior. No game-specific fields are added to the contract.

## Panel and canvas boundary

The game panel and the main canvas are separate frontend surfaces. Click-and-drag
interactions in the game panel stay within that panel. They submit ordinary game
actions; communication with the main canvas flows through committed game state
and the engine's canvas API. There is no direct cross-surface interaction path.

## Read interface

`canvas` is an immutable JSON-compatible CEL map. All root fields below are
present. Maps and lists are empty when there are no entries; `page` and `event`
are explicitly nullable. IDs are opaque strings, never filesystem paths or URLs.

```json
{
  "version": 1,
  "revision": 42,
  "page": {
    "id": "page-7",
    "scene_id": "waking-room-door",
    "asset_id": "scene:waking-room-door:1",
    "media_kind": "image",
    "animation": null,
    "width": 1536,
    "height": 1024
  },
  "stamps": {
    "placement-3": {
      "id": "placement-3",
      "asset_id": "stamp:brass-key",
      "managed_by": "user",
      "game_key": null,
      "draggable": true,
      "center": {"x": 7200, "y": 4800},
      "bounds": {"x": 6900, "y": 4600, "width": 600, "height": 400},
      "rotation": 0
    }
  },
  "drawings": {},
  "annotations": {},
  "regions": {
    "door-lock": {
      "id": "door-lock",
      "bounds": {"x": 6800, "y": 4200, "width": 1000, "height": 1600}
    }
  },
  "tray": ["stamp:brass-key"],
  "overlay": {"items": [], "filters": []},
  "event": {
    "id": "interaction-19",
    "kind": "stamp_placed",
    "actor": "player",
    "page_id": "page-7",
    "canvas_revision": 42,
    "object_ids": ["placement-3"],
    "point": {"x": 7200, "y": 4800},
    "region_ids": ["door-lock"],
    "key": null,
    "binding_id": null
  }
}
```

| Field | Contract |
| --- | --- |
| `version` | Integer interface version, initially `1`. |
| `revision` | Persisted, monotonically increasing revision of the exposed shared canvas. Independent of game revision. |
| `page` | Active shared/orator page, or `null`. `id` identifies a history page; revisiting it preserves its ID. `scene_id` is the canonical authored scene name or `null` for media without an authored scene association. `asset_id` identifies the exact media revision. `media_kind` is `image`, `animation`, or `video`; positive integer `width`/`height` describe its coordinate plane. `animation` is `null` for an image, otherwise `{id, format, playback}` as specified below. |
| `stamps` | Current-page placements keyed by placement ID. Each has `id`, `asset_id`, `managed_by` (`user` or `game`), nullable `game_key`, `draggable`, `center`, `bounds`, and `rotation`. Multiple placements may reference the same asset. IDs survive moves; deletion followed by replacement creates a new ID. |
| `drawings` | Current-page completed strokes keyed by stable stroke ID. Each has `id`, `points` (ordered `{x, y}` pairs), and `bounds`. No inferred circle, arrow, or intent is implied. |
| `annotations` | Current-page text annotations keyed by stable annotation ID. Each has `id`, `text`, and `bounds`. |
| `regions` | Visible interaction regions for this exact scene asset, keyed by authored region ID. Each has `id` and rectangular `bounds`. Regions carry geometry, not hidden contents, prerequisites, or puzzle solutions. |
| `tray` | Ordered asset IDs currently offered in the shared stamp tray, excluding private library entries. Availability is presentation; ownership is game state. |
| `overlay` | Current page's game-overlay layer as `{items, filters}`, using the output schemas below. Both lists are always present, even when empty. This layer is separate from stamps and user drawings. |
| `event` | The host-bound canvas interaction being submitted, or `null` for an action without canvas evidence and during ordinary rendering. |

With no active page, all page-scoped collections are empty. Tray contents may
still be present. Asset handles resolve through the theater's catalog; user
assets get opaque host handles in a separate namespace. Packages cannot choose
an arbitrary asset URL. This interface exposes neither other pages' annotations
nor private viewer cursors, captures, chat, model prompts, or hidden game state.

### Coordinates and identity

All geometry uses integer image-relative coordinates: the top-left is `(0, 0)`
and the bottom-right is `(10000, 10000)`, independent of browser zoom, pan, device
pixels, or game-panel size. Conversion rounds to the nearest integer, with halves
away from zero. `rotation` is clockwise millidegrees in `[0, 360000)`.

Bounds are axis-aligned visual bounds after rotation, before clipping. Objects
may extend outside the image, so bounds origins may be negative and dimensions
may exceed 10000. Pointer points and stamp centers are inside the image. Stroke
points are inside the image; sizes are nonnegative. Width/height of rectangular
regions and overlays must be positive; region bounds stay inside the image.
Using bounds for a stamp does not establish opaque-pixel contact or correct fit.

Geometry is derived from the same image-plane transform used by the renderer.
The host must replace the current viewport-dependent stamp/text sizing with
canonical image-plane dimensions before exposing reliable bounds to rules.
Reading existing `x`/`y` fields alone is insufficient to implement this contract.

Scene IDs identify authored views, asset IDs identify exact artwork, page IDs
identify history instances, and placement/stroke/annotation IDs identify objects.
None of these identities is interchangeable. A new artwork revision requires its
own region geometry; regions are never silently reused across asset revisions.

### Still-image catalog and authored regions

The still-image adapter uses the scene directory name as `scene_id`, such as
`references/scenes/waking-room-door/`. Game stamp handles use `stamp:<image-stem>`
under the theater's `stamps/` directory; ambiguous stems fail resolution. These
are catalog lookups, not filesystem paths accepted from CEL. Exact page media
uses an opaque content-addressed handle and an immutable theater-local copy.

Author regions in that scene's `scene.yaml`, keyed by the selected image's exact
SHA-256 digest. An artwork revision with no matching entry exposes no regions.
For example, replacing the example digest with the actual artwork digest:

```yaml
canvas:
  artwork:
    "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa":
      door-lock:
        id: door-lock
        bounds: {x: 6800, y: 4200, width: 1000, height: 1600}
```

Region mapping keys must match their IDs. Geometry is validated before exposing
it. The catalog retains exact bytes for an already selected page even when the
source scene image is replaced later. Catalogued animations require the separate
animation adapter described below.

### Evaluation scope

For an opted-in package, `canvas` is available in transition `available`, `guard`,
`bindings`, and `result`, the new `canvas_effects` expression, and view expressions.
It is reserved and cannot be shadowed by package bindings or view repeat bindings.
Author functions retain explicit parameters: pass `canvas` or a subset as an
argument instead of giving functions implicit host access.

State invariants, top-level `derived`, and `model_state` remain independent of
`canvas`. Invariants describe valid persisted game states even after unrelated
drawings change. Canvas-dependent convenience calculations belong in transition
bindings or explicit author functions. Accepted observations can be copied into
`state` when a game needs to remember them or expose them to narration.

All expressions in an attempt see the same snapshot. Action availability and
rendered views refresh when that snapshot changes, even if game revision does
not. Rendering sees `event: null`; it must never consume an interaction.

## Interaction evidence

Event kinds in v1 are `stamp_placed`, `stamp_moved`, `stamp_removed`,
`drawing_completed`, `annotation_changed`, `annotation_removed`, and
`region_activated`, and `key_pressed`. Each event has exactly the fields shown
above. `point`, `key`, and `binding_id` are nullable; `object_ids` and `region_ids`
are always lists. `key` and `binding_id` are non-null only for `key_pressed`.
`page_id` is nullable for keyboard input with no active page. The host supplies the
authenticated `actor` (`player`, `contributor`, or `narratron`) and retains the
principal identity internally for permissions and evidence ownership checks.

Canvas edit events describe completed edits, not every pointer movement.
Their snapshot is after the edit, before any game response; removed IDs are
consequently absent from the snapshot. `point` is the final pointer location when available.
`region_ids` contains every visible region containing that point, ordered by ID,
with edges included. Overlap is not an implicit choice of target. Region
activation supplies the explicitly activated region and its center as the point.

The browser or model proposes a named transition and its ordinary `action`
arguments. The host binds a recorded event to the request; a caller cannot supply
its own trusted `canvas`, actor, region hit, or event contents. Raw drawing shape
and semantic intent are distinct. A model's proposed target remains an argument
to validate; this interface does not certify that a stroke is an arrow or that
an image depicts a keyhole. Packages may use authored geometry, explicit region
activation, or a separately specified interpretation step.

Actions from the panel, speech, or Narratron can use `event: null`. If a rule
requires placement evidence, its guard must require and inspect the bound event.
Permission to interpret a player's gesture does not transfer actor authority to
Narratron; interpreted player actions retain their original actor and principal.
Canvas actions obey the same bounded-turn or mutual-acceptance rules as panel
actions. Inspecting and experimenting need not trigger narration each time.

Example guard, for a game whose state includes `inventory` and `door`:

```cel
canvas.page != null &&
canvas.page.scene_id == "waking-room-door" &&
canvas.event != null &&
canvas.event.kind in ["stamp_placed", "stamp_moved"] &&
action.placement_id in canvas.event.object_ids &&
action.placement_id in canvas.stamps &&
canvas.stamps[action.placement_id].asset_id == "stamp:brass-key" &&
"door-lock" in canvas.event.region_ids &&
"brass-key" in state.inventory &&
state.door == "locked"
```

Here the rule intentionally accepts the gesture's final point inside the region;
games requiring the stamp center or its full bounds inside must check that
geometry explicitly. The host also verifies that the event's principal was
permitted to manipulate the placement. This guard establishes an attempt to
install the owned key; its result must record installation, without implicitly
turning it or moving through the door.

## Keyboard input

An opted-in game may declare `keyboard` next to `transitions` and `view` in its
package. This is an input declaration, independent of `canvas_api.effects`.
Keys dispatch existing named transitions; packages do not supply keyboard
handlers or JavaScript. Arrow-key movement remains a sequence of discrete,
validated moves under the game's turn rules.

```yaml
keyboard:
  enabled: 'state.phase == "exploring" && state.turn == "player"'
  bindings:
    - id: move-north
      key: ArrowUp
      label: Move north
      action: {name: '"move"', arguments: '{"direction": "north"}'}
    - id: move-south
      key: ArrowDown
      label: Move south
      action: {name: '"move"', arguments: '{"direction": "south"}'}
    - id: move-west
      key: ArrowLeft
      label: Move west
      action: {name: '"move"', arguments: '{"direction": "west"}'}
    - id: move-east
      key: ArrowRight
      label: Move east
      action: {name: '"move"', arguments: '{"direction": "east"}'}
```

`keyboard.enabled` is an optional CEL boolean expression, defaulting to `true`.
Each binding has required literal `id`, `key`, and player-visible `label`, an
optional CEL boolean `enabled` expression defaulting to `true`, and an `action`
using the existing view-action contract. Its `name` and `arguments` are CEL
expressions; its permission and confirmation behavior match a panel button.
Keyboard bindings can reference only player/contributor transitions, never
Narratron-only transitions. Binding IDs and keys must be unique in the package.

V1 supports the logical keys `ArrowUp`, `ArrowDown`, `ArrowLeft`, `ArrowRight`,
`Enter`, a single space (`" "`), and single ASCII letters or digits. Letter
bindings are normalized to lowercase, including incoming key values. Keys with
Shift, Control, Alt, or Meta modifiers are ignored, as are composition input and
reserved navigation keys such as Tab and Escape. A package may define at most
64 bindings. ID and label lengths are limited to 128 characters.

`enabled`, action names, and arguments evaluate against the current committed
state and canvas snapshot, with `canvas.event: null`. They refresh whenever
either revision changes. The host renders a bounded list of resolved shortcuts
and labels to the client, exposing no additional hidden state. Disabled or
unauthorized bindings are inactive. Disabling a binding is a UI aid; transition
guards and permission checks remain authoritative.
Resolved shortcuts share the existing 128 KiB materialized-view budget and
4 KiB individual-action limit; overflow reports an error without truncation.

### Focus and dispatch

Opening the game panel activates its keyboard shortcuts for the current tab.
Closing the panel leaves them available when the canvas itself has focus.
The panel lists the shortcuts, and a focused canvas shows a small controls hint.
Escape releases game-key capture; opening the panel again reactivates it. This
is local focus state and changes neither game nor canvas revision.

The keyboard router handles a key only when the canvas surface or game-panel
background has focus. Text inputs, chat, textareas, contenteditable areas,
buttons, tabs, sliders, stamp rotation handles, menus, dialogs, and other
interactive descendants retain their own keyboard behavior. A pointer drag,
selection transform, or active canvas text edit also suspends game shortcuts.
Other focused applications and background browser tabs never dispatch them.

One key press produces at most one action: ignore native key-repeat events
until release, and permit only one outstanding keyboard submission per client.
Additional presses during submission or confirmation are dropped, not queued or
replayed after a state change. The router prevents default scrolling only for
an enabled binding it owns in the current focus context. It does not also move
a selected stamp, change tabs, or navigate image history for that same key.
Blur, page hiding, Escape, or package reset clears held-key tracking; resuming
requires a fresh non-repeated press. V1 has no key-up actions or held-key state.

For a matched key, the client submits the binding ID, observed game and canvas
revisions, and a unique client input ID. The host resolves the binding and its
arguments against that snapshot, records a `key_pressed` event, and dispatches
the ordinary game action. It derives actor and permissions from the session,
not from client fields. The event has the canonical `key` and `binding_id`,
empty `object_ids` and `region_ids`, and `point: null`; it carries the current
page ID or `null` if there is no page. Input registration alone does not advance
canvas revision. Retries reuse the same input/event identity and cannot move twice.

The browser reports input; this is not proof of a physical key press. The host
validates the declared shortcut and resulting action just as it validates a
button click. It accepts no arbitrary client-supplied trusted event contents.
Only matched game shortcuts enter this flow; ordinary typing is not recorded.
Stale or illegal movement returns ordinary action feedback and does not retry
against a newer position automatically. A `move` transition still checks route,
obstacles, movement budget, actor, and turn ownership, and can publish stamp or
overlay changes with the same commit. Its `triggering` flag determines whether
the committed move prompts narration.

## Output interface

Transitions may add `canvas_effects`, a CEL expression returning a list of typed
commands. It evaluates after `result`, against the same observed `canvas`, with
`state` still the pre-action state and a reserved `next_state` binding holding
the validated result. `next_state` is available only in this expression. The
host validates the entire command list before committing anything.

| Operation | Exact fields and behavior |
| --- | --- |
| `show_scene` | `{op, scene_id, page_ref}` pushes an existing authored scene's catalogued asset as a new active page. `page_ref` names the new page within this command list. Pins or visual ownership conflicts reject the action rather than silently queue its requested scene. The new page starts without stamps, drawings, annotations, or overlays; the previous page and its contents remain in history. |
| `show_animation` | `{op, animation_id, playback, page_ref}` pushes an existing catalogued animation as a new active page. `playback` is `once` or `loop`. It shares `show_scene`'s pin checks, history behavior, empty initial page, and local page-reference semantics. |
| `put_stamp` | `{op, target, key, asset_id, center, width, rotation, draggable}` creates or updates a game-managed stamp on the target page. `key` is a stable game-chosen placement key, distinct from its asset ID. |
| `remove_stamp` | `{op, target, key}` removes the game-managed placement with that key from the target page. A missing key is a successful no-op. |
| `set_stamp_tray` | `{op, asset_ids}` replaces the game's offered tray with an ordered list of existing stamp asset handles. Other user/library entries remain available independently. `canvas.tray` describes the effective offered tray, with duplicate handles removed. |
| `set_overlay` | `{op, target, items, filters}` replaces the target page's complete game-overlay layer. Empty `items` and `filters` lists clear it. |

### Page targets and stamp writes

A `target` has exactly one field:

- `{"page_id": canvas.page.id}` addresses the observed active page. Other history
  pages are not writable through this version of the interface.
- `{"page_ref": "arrival"}` addresses a new page created by an earlier
  `show_scene` or `show_animation` command with `page_ref: "arrival"` in the same list.

Commands apply in list order to a staged canvas. Page references are local to
the list, never persisted as page IDs, and cannot refer forward. This lets one
action push a scene or animation and populate it with stamps without another game turn.
The CEL snapshot remains unchanged during evaluation; new placements appear in
`canvas.stamps` on the next read. The committed receipt includes the resulting
canvas revision and the mapping of page references to durable page IDs.

`put_stamp.center` uses the read interface's normalized coordinates. `width` is
the positive unrotated stamp width in the same units; the host derives height
from the asset aspect ratio and target page dimensions, then computes rotated
bounds. `rotation` uses clockwise millidegrees. `draggable` is a boolean.

Placement keys are scoped to the loaded game package and target page. Repeated
`put_stamp` commands in later actions update the same placement and preserve its
host-generated ID. Distinct keys allow several copies of the same asset. The
read interface exposes these stamps alongside user stamps with `managed_by` set
to `"game"` and `game_key` equal to the authored key; user stamps have `game_key`
set to `null`. A removed placement that is later recreated receives a new ID.

Game-managed stamps are persisted, broadcast, rendered into captures, and
included in later canvas observations. They are actual canvas objects, distinct
from the visual-only stamp overlays below. When `draggable` is true, permitted
players can move them and produce ordinary `stamp_moved` evidence. When false,
movement requires a game write command. Direct user deletion of game-managed
stamps is not permitted; a game action may remove them. Game commands identify
only game-managed placements and cannot overwrite a user's stamp by choosing
its placement ID. Managing a canvas object does not establish inventory ownership.

V1 permits at most one page push (`show_scene` or `show_animation`) and one
`set_stamp_tray` per transition, and one `set_overlay` per target page. Several
stamp writes are allowed, but a target page/key pair may appear in at most one
stamp write in a list; contradictory
updates reject the entire action. Every operation must appear in
`canvas_api.effects`.
These are package capabilities, not additional per-action user approval prompts.
Omitting `canvas_effects` is equivalent to returning `[]`.

```yaml
# Fragment inside a transition whose result records arrival in the room.
canvas_effects: |-
  [
    {
      "op": "show_scene",
      "scene_id": "waking-room",
      "page_ref": "arrival"
    },
    {
      "op": "put_stamp",
      "target": {"page_ref": "arrival"},
      "key": "key-on-peg",
      "asset_id": "stamp:brass-key",
      "center": {"x": 7200, "y": 4800},
      "width": 600,
      "rotation": 0,
      "draggable": true
    }
  ]
```

A later transition can move that stamp on the current page:

```yaml
canvas_effects: |-
  [{
    "op": "put_stamp",
    "target": {"page_id": canvas.page.id},
    "key": "key-on-peg",
    "asset_id": "stamp:brass-key",
    "center": {"x": 5000, "y": 6500},
    "width": 600,
    "rotation": 90000,
    "draggable": false
  }]
```

### Game-overlay layer

Each page has one overlay layer owned by the loaded game package. It renders
symbols, text, image badges, and highlights on the same normalized coordinate
plane as the scene. It works over still images and animations. Game actions
publish the entire layer through `set_overlay`; CEL can read its last committed
contents through `canvas.overlay`. To change only items or only filters, include
the other existing list in the command. Rendering and view refreshes never write
the layer themselves.

Every item has `id`, `kind`, `bounds`, and integer `opacity` in `[0, 10000]`.
IDs are unique within the layer; list order determines drawing order, last on
top. Bounds use the shared image-relative coordinate system. Each kind adds
these required fields:

| Item kind | Additional fields |
| --- | --- |
| `symbol` | `symbol`, `color`, `rotation`. The symbol is one of `circle`, `ring`, `cross`, `arrow`, `diamond`, `star`, or `check`, rendered as a host-owned vector primitive. Rotation uses clockwise millidegrees. |
| `text` | `text`, `color`, `font_size`, `align`. Text is literal, `font_size` is a positive integer in image-height units out of 10000, and `align` is `left`, `center`, or `right`. Host-font text wraps and clips within its bounds. |
| `region` | `region_id`, `color`. Highlights an existing visible interaction region; item bounds must equal that region's bounds. |
| `stamp` | `asset_id`, `rotation`. Renders a visual badge from a catalogued stamp asset, fitted within the item bounds with its aspect ratio preserved. It does not create a draggable placement in `canvas.stamps`. |

Colors are literal six-digit `#RRGGBB` values. The host owns fonts, symbol paths,
and styling; the interface does not accept HTML, arbitrary CSS, scripts, or URLs.
Overlay items pass pointer events through to the canvas and do not create
actions, inventory, or interaction regions. The game panel continues to provide
the game's controls; overlay output communicates the results on the canvas.

Filters apply to the base scene or animation before compositing stamps,
drawings, text annotations, and overlay items. They do not affect the game panel,
chat, cursors, or other application controls. Filter entries have `kind` and
integer `amount` in `[0, 10000]`; `tint` additionally requires `color`.

| Filter kind | Meaning of `amount` |
| --- | --- |
| `dim` | Black tint opacity, from unchanged at 0 to fully black at 10000. |
| `tint` | Specified color's tint opacity, from 0 to full coverage. |
| `grayscale` | Mix between original color and grayscale. |
| `sepia` | Mix between original color and the host's sepia treatment. |
| `blur` | Blur radius from 0 to 1% of the image's shorter side, scaled with the image. |
| `vignette` | Opacity of the host's fixed black radial edge mask, clear at the center. |

Filters compose in list order, with no duplicate kind in a list. Their
parameters are committed presentation state; they do not vary with a clock or
change game mechanics. The renderer uses the same filter and overlay composition
for shared display and agent captures, without changing the source media.

The visual stack is base media with filters, canvas stamps, user drawings and
text, game-overlay items, then application controls. The overlay remains visible
when the panel closes and is persisted with its page. Revisiting history restores
that page's layer; pushing a new page starts with an empty layer. A later game
action can replace or clear it. Resetting the game clears its page overlays;
changing packages hides the previous package's overlays. Neither operation
removes user content. There is no automatic expiry or implicit redraw from game
state in v1.

```yaml
# Fragment inside a transition that marks the route and changes the atmosphere.
canvas_effects: |-
  [{
    "op": "set_overlay",
    "target": {"page_id": canvas.page.id},
    "items": [
      {
        "id": "exit-marker",
        "kind": "symbol",
        "bounds": {"x": 7000, "y": 3500, "width": 800, "height": 800},
        "opacity": 10000,
        "symbol": "arrow",
        "color": "#FFD166",
        "rotation": 0
      },
      {
        "id": "exit-label",
        "kind": "text",
        "bounds": {"x": 6000, "y": 4500, "width": 2800, "height": 700},
        "opacity": 10000,
        "text": "The lift is ready",
        "color": "#FFD166",
        "font_size": 300,
        "align": "center"
      }
    ],
    "filters": [{"kind": "tint", "color": "#19324D", "amount": 2500}]
  }]
```

### Animation playback

`show_animation` resolves an existing animation handle through the theater's
catalog; it does not generate media. The adapter supports the host's `triframe`,
`layered`, and `video` formats. `page.animation` contains the canonical animation
`id`, its `format` (one of those three values), and requested `playback`. A
video-backed page has `media_kind: "video"`; other animations use `"animation"`.
`page.asset_id` pins the exact manifest and media revision, and dimensions describe
one fixed coordinate plane shared by frames, drawings, stamps, and overlays.

Playback starts when the committed page is presented. `once` plays one authored
cycle and holds its final composition; `loop` repeats until another visual is
selected. Timing comes from the validated catalog manifest. Frame progression
does not change canvas revision, create game events, or advance game rules.
Game outcomes commit without waiting for playback to finish. A viewer joining
later or revisiting history starts local playback; game logic cannot depend on
viewers sharing a frame or reporting completion. If playback is unavailable,
the host displays the catalogued fallback image and preserves the committed page.

Stamps stay above the animation on its fixed coordinate plane. Interaction
regions must describe fixed authored targets valid throughout playback; v1
does not expose moving hotspots, current frame indices, or pixel-based collision.
Output recovery reuses the committed page and playback identity rather than
creating another page or restarting playback for viewers already displaying it.

```yaml
canvas_effects: |-
  [{
    "op": "show_animation",
    "animation_id": "clockwork-lift-rising",
    "playback": "once",
    "page_ref": "lift"
  }]
```

A following `put_stamp` or `set_overlay` command can target `{"page_ref": "lift"}`
in the same list, just as it can target a newly pushed still scene.

The host owns media display, persistence, and notification. CEL never invokes
HTTP, reads pixels, mutates `canvas`, or performs arbitrary host calls. Output
application does not create new player events or recursively trigger game actions.
Preparation resolves scene and asset handles to exact catalog revisions and
records those resolutions with the commands, so a retry cannot select new art.
For animations, resolution also validates the manifest, constituent media,
timing, dimensions, requested playback support, and fallback image.

## Submission, commitment, and replay

Canvas-enabled action requests extend the existing request with `canvas_revision`
and nullable `canvas_event_id`. The host resolves evidence, checks permissions,
and captures a snapshot under a shared commit protocol. It validates both game
and canvas revisions again at commitment. A changed page, snapshot, or game
position rejects the action as stale; a rejected action leaves game state and
game-requested canvas output unchanged. The original user edit remains visible.

A successful commit durably records next game state, its new game revision,
the consumed event ID and action fingerprint, and the validated canvas output in
one transaction. Canvas revision advances once if exposed canvas content changes.
Event consumption alone does not change canvas revision. Exact submission retries
return the saved receipt before checking now-stale revisions. Reusing a consumed
event with different arguments or another transition fails. Rejected submissions
do not consume the event and may be corrected against an unchanged snapshot.

After persistence, display/notification failures retry the same output by commit
ID without repeating the action. Recovery restores pending output before accepting
new canvas-dependent actions. The host must not claim successful commitment with
only game state or only canvas output saved. Retain input snapshots and event
evidence with commit records for replay; replay never reads today's live canvas.

Private previews produce no shared effects and consume no events. In v1, games
opting into `canvas_api` use server evaluation without local draft submission;
browser preview support requires a later explicit extension. Existing games keep
their existing preview behavior. Resource-budget retries reuse the captured
snapshot and apply output only after a successful commit.

## Bounds and implementation requirements

The snapshot is bounded to 128 KiB, 256 stamps, 256 drawings with at most 200
points each, 128 annotations of at most 2000 characters each, 128 regions, 256 tray
entries, 128 overlay items, and eight filters. Output is bounded to 16 KiB and 64 commands;
overlay text shares the annotation length limit. IDs are at most 128 characters.
The staged result must satisfy the snapshot limits before commitment; writes
that would exceed them reject the entire action.
All numbers are integers within JavaScript's exact integer range. The host never
silently truncates evidence: an oversized canvas disables canvas-dependent action
evaluation with a specific error until the page is reduced. Story/chat and games
without canvas access remain usable. These are host limits, not package settings.

Implement the contract consistently in the Python evaluator and Go CEL worker,
including preparation, context refresh, validation, commit recovery, and save/load.
The current canvas coordinator already holds visual, doodle, and game state, but
the game runtime does not currently receive their observation snapshot. Existing
separate game/doodle writes need the shared transaction protocol described above.
Stable stroke IDs, canonical object dimensions, authored region catalogs, and the
output operations also require implementation; they are not existing promises.

Acceptance checks must cover an empty canvas, consistent coordinates across
viewports, moved/removed objects, overlapping regions, unowned stamps, actor
forgery, stale pages and both revision conflicts, duplicate delivery, failed
persistence, output recovery, resource limits, and parity between evaluators.
Write checks must also cover scene-plus-stamp batches, page-reference resolution,
stable placement updates, game/user placement separation, draggable permissions,
idempotent removal, and retrying output without duplicating a page or stamp.
Animation checks cover all three formats, once/loop playback, animation-plus-stamp
batches, fallback images, history restoration, and frame progression leaving game
state and canvas revision unchanged.
Overlay checks cover all item and filter kinds, normalized text sizing, filter
order, composition over animations, capture parity, pointer pass-through, panel
closure, page history/reset behavior, and atomic replacement or clearing.
Keyboard checks cover all four arrow directions, panel shortcuts and canvas
focus, typing and nested-control exclusion, Escape/blur, held-key repeats,
in-flight input dropping, disabled bindings, contributor permissions, stale
revisions, duplicate submissions, and guards enforcing blocked routes and turns.
Boundary checks cover panel-only drags, rejected-preview restoration, panel
actions producing committed canvas effects, canvas inputs refreshing the panel
through game state, and operation without frontend access between the surfaces.
The Waking Room is a suitable first integration: inspect, acquire, place,
operate, and move while the game panel remains closed.
