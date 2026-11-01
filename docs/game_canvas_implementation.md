# Game/canvas interface review and implementation

The target remains the complete [v1 interface](game_canvas_interface.md), including
shared objects, atomic commits, playback, overlays, keyboard input, and the Waking
Room integration. The evaluator work below is a prerequisite, not completion of
the interface.

## Spec review

The core boundary is sound: persisted game state owns mechanics, the canvas is
an observation, and CEL returns commands instead of making host calls. Commands
must join the same durable commit as game state. Sharing browser objects between
the panel and canvas would bypass this boundary.

Resolve these contract gaps when implementing the host:

1. **P1 — Retry identity for event-free actions.** Submission/replay promises saved
   receipts for exact retries, but only adds `canvas_revision` and nullable
   `canvas_event_id` to the request. Panel, speech, and Narratron actions may have
   no event. Give every submission a stable request ID, scoped to the authenticated
   principal and game instance, and store its fingerprint and receipt. A retry
   must look up that record before checking revisions. Reusing an ID with a
   different fingerprint must reject. Keyboard input IDs should use this same
   mechanism.
2. **P1 — Bind event freshness to the captured observation.** The request revision
   checks alone do not prohibit submitting an old, unconsumed event alongside
   today's canvas revision. Require the stored event's page and canvas revision
   to equal the captured observation, and scope event identity to the game
   instance and original principal. Otherwise an old placement gesture could be
   applied after the page or placement has changed. The new snapshot validator
   checks the page/revision portion; principal and instance checks remain host
   responsibilities.
3. **P2 — Define the lifecycle of game-managed content.** The overlay section
   defines reset and package-switch behavior, but equivalent rules are missing
   for game stamps and the game's tray contribution. Specify whether reset
   removes them, and whether switching packages hides or removes old content.
   Include all history pages, durable game-instance identity, and loading saved
   games. User content must survive. A package's previous stamp keys must not
   accidentally become a fresh game's objects.
4. **P2 — Make explicit region activation unambiguous.** The event section says
   `region_ids` contains every region hit by `point`, then says region activation
   supplies the explicitly activated region and its center. When regions overlap,
   those definitions do not identify the same target. Define `region_activated`
   as carrying only the explicitly chosen region, or add a distinct target field.
   Keep ordinary point-hit evidence inclusive and sorted.

Game actions and doodle mutations still persist separately. The new observation
adapter migrates saved objects to canonical dimensions, preserves stroke
identities through snapshots, and pins still-image media. `theater.json` now uses
an atomic replacement. These prerequisites still need the shared action commit
protocol; they do not by themselves make game actions transactional.

## Implemented foundation

- `game_engine/canvas_contract.py`: strict versioned observations, typed command unions,
  integer geometry, media consistency, identity checks, evidence freshness,
  resource limits, page-reference ordering, capabilities, stamp-write conflicts,
  overlay/filter validation, and canonical shortcut keys.
- `game_engine/game_runtime.py`: package declarations for `canvas_api`, `canvas_effects`,
  and `keyboard`; reserved binding checks; restrictions on canvas-dependent
  invariants/derived/model state; explicit author-function parameters; empty
  initial observations during package validation.
- `game_engine/canvas_evaluation.py` and the Go worker: pure canvas-aware preparation and
  action evaluation. Both engines read one snapshot, expose `next_state` only to
  output evaluation, return commands, and render with `event: null`. Shortcuts
  resolve through the existing action contract and share the view budget.
- Canvas-enabled packages do not export browser preview programs. The legacy
  `GameState.act` path rejects canvas-enabled actions until a shared commit host
  is connected, so it cannot accept a move while silently discarding commands.
- `components/canvas/canvas_assets.py`: exact content-addressed still-image
  revisions, scene and stamp handle resolution, and region catalogs pinned to
  an artwork digest. Replacing source art cannot change an already observed page.
- `components/canvas/canvas_observation.py` and `canvas_geometry.py`: a projection
  of real page objects with persisted revision tracking, canonical stamp/text
  dimensions, aspect-correct rotation, and stable stroke identities. Deleting
  middle segments creates stable path components instead of invented connections.
  Missing geometry/media and oversized evidence fail explicitly.
- `CanvasStateManager.canvas_observation()` migrates and persists those native
  page objects. The renderer uses their canonical dimensions and fitted image
  plane for committed display and selection. Browser checks cover contain/cover
  layouts, resize, device pixel ratio, pan, and zoom. Stamp capture uses those
  dimensions too; canonical text clips to its recorded layout box.
- `utils/atomic_file.py`: flushed temporary files and atomic replacement of the
  theater document. Failed writes retain the previous readable document.
  A storage-mirror notification failure after local commitment is logged and
  remains retryable, without reverting the already saved game/canvas state.
  The future commit outbox must make notification recovery durable across restarts.

The stashed changes were restored after the engine package rename to
`game_engine`; the newer doodle action/undo refactor remains in place.

These modules do not yet make game output visible on the shared canvas.

Verification on 2026-11-01 after restoring the stash and completing the geometry
work: `uv run pytest` passed all 3,609 core tests. `go test ./...` in
`game_engine/cel_go_worker`, Ruff on the engine and changed Python modules, and
`git -c core.whitespace=cr-at-eol diff --check` also passed. The worker was rebuilt
locally for the Python/Go parity tests. Browser coverage includes history loading,
undo, stamps, annotations, and contain/cover geometry through resize and zoom.
This evidence covers the implemented foundation, not the remaining acceptance
gates below.

## Remaining implementation and evidence required

1. **Canonical shared canvas adapter.** Migrate stamp/text dimensions, add stable
   stroke IDs, resolve opaque exact-revision asset handles, and define authored
   region catalogs. Derive observations from the actual renderer's image plane.
   Verify identical geometry across zoom, pan, aspect ratios, and client sizes;
   verify empty and oversized pages without silent truncation. The still-image
   adapter and committed geometry are implemented. Live coordinate conversion for
   drawing, text editing, and stamp placement uses the fitted plane. Finish full
   input/observation round trips, completed-stroke boundaries, catalogued animations, and capture/font parity;
   connect observation refresh to every exposed mutation under the shared lock.
   Bind native user/theater stamp IDs to the trusted catalog before enabling live
   rules; do not use caller-supplied URLs or ambiguous image stems as proof of
   asset identity. Canonicalization currently uses the existing capture resolver.
2. **Shared transaction and event store.** Route every exposed canvas mutation
   through one revision/locking protocol with game actions. Capture authenticated
   evidence, retain replay snapshots, stage commands and resolved assets, validate
   both revisions, and atomically persist game state, canvas state, event
   consumption, and retry receipts. Verify stale events/pages/revisions,
   conflicting retries, failed writes, concurrent edits, and recovery after
   restarting. Notification/storage-mirror failure after commitment must not roll
   back an already durable action.
3. **Output adapters.** Apply scenes, all three animation formats, game stamps,
   the tray contribution, and page overlays to existing canvas/history storage.
   Enforce pin/ownership checks and game_engine/user separation. Reprepare the committed
   view against the staged result. Verify scene-plus-stamp batches, exact asset
   revisions, stable placement updates, removal, draggable permissions, fallback
   images, and output retry without duplicate pages or placements.
4. **Renderer and captures.** Render canonical dimensions, all overlay items and
   filters, and playback in both browser display and captures. Preserve pointer
   pass-through, history, panel closure, and package/reset lifecycle. Verify
   once/loop behavior and that frames never advance canvas or game revisions.
5. **Input/API and refresh.** Extend submissions and receipts, bind server-owned
   events, refresh the panel on either revision, and wire keyboard dispatch with
   the specified focus, modifiers, held-key, confirmation, and in-flight rules.
   Preserve authenticated contributor permissions and interpreted player identity.
   Verify typing/nested controls, blur/Escape, stale inputs, duplicate delivery,
   turn/route guards, and no direct cross-surface calls or drags.
6. **Integration and regression gates.** Run the complete core suite plus explicit
   Waking Room adventure tests. Demonstrate inspect, acquire, place, operate,
   and move with the panel closed, including save/load and process recovery.
   Audit every acceptance check in the spec before declaring the goal complete.
