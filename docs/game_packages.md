# Theater game packages

`game/game.json` declares a game's state, transitions and view. Narratron has no
built-in game rules or game-specific renderer. The example adventure contains a
checkers implementation; `testing/game_fixtures.py` declares an unrelated counter
using the same runtime and renderer.

The entry point is mounted by adventure selection, folder import and ZIP import.
The canvas game button appears when a package is present and opens only on click.
The active orator can submit actions; when the active orator enables collaboration
mode, viewers with contributor permission can also submit actions annotated with
`"permission": "contributor"`. Other viewers see the committed view.
A live story agent is not required.

## Minimal package

All strings in `props`, and the `name` and `arguments` of a view action, are CEL
expressions. Literal text therefore includes CEL string quotes.

```json
{
  "version": 2,
  "initial": {"count": 0},
  "state_schema": {
    "type": "object",
    "properties": {"count": {"type": "integer", "minimum": 0, "maximum": 10}},
    "required": ["count"],
    "additionalProperties": false
  },
  "transitions": {
    "increment": {
      "permission": "contributor",
      "parameters": {"type": "object", "additionalProperties": false},
      "guard": "state.count < 10",
      "result": "{\"count\": state.count + 1}"
    }
  },
  "view": {
    "kind": "column",
    "children": [
      {"kind": "text", "props": {"text": "\"Count: \" + string(state.count)"}},
      {
        "kind": "button",
        "props": {"text": "\"Add one\"", "disabled": "state.count >= 10"},
        "action": {"name": "\"increment\"", "arguments": "{}"}
      }
    ]
  }
}
```

## State and transitions

- `initial`: an arbitrary JSON object. There are no reserved game field names.
- `instructions`: optional game-specific guidance shown to the story planner
  with the current game state. Explain when the Narratron side should use its
  actions and which projected fields are authoritative.
- `model_state`: optional CEL expression that projects the complete game state
  into a concise JSON value for story modules. The full state remains persisted
  and validated; this projection is limited to 16 KiB. If omitted, story modules
  see the full state.
- `state_schema`: JSON Schema for every initial, restored and resulting state.
- `constants`: optional immutable JSON data available to expressions.
- `functions`: ordered named pure CEL expressions with `parameters` and `expr`.
  A function may call earlier functions, but not itself or later functions.
  It receives its parameters and `constants`; it has no implicit access to state.
- `derived`: ordered `{ "name": "...", "expr": "..." }` bindings, evaluated from
  `state`, `initial` and `constants`. These values are not persisted.
- `invariants`: CEL boolean expressions checked against state and derived values.
- `transitions`: author-named actions. Each declares a `parameters` JSON Schema,
  optional boolean `guard`, optional ordered `bindings`, and a `result`
  expression returning the **complete next state object**. Action arguments are
  available as `action`. Bindings cannot overwrite names already in scope.
- Each transition can set `actor` to `"player"`, `"contributor"`, or `"narratron"`,
  `permission` to `"orator"` (default) or `"contributor"`, and include a short
  `description`. Setting `permission: "contributor"` (or `actor: "contributor"`)
  allows viewers with contributor permission to invoke the action on the canvas
  whenever the active orator has Collaboration Mode enabled. A button's `action`
  in `view` can also set `permission: "contributor"` to annotate a specific view
  action.
  An optional state-only `available` CEL expression controls whether a Narratron
  action appears in the planner's current action list. The transition's `guard`
  remains the authoritative check when an action is submitted.
  StoryRecorder can execute Narratron actions while recording either a completed
  story response or a committed player game action. Encode prerequisites such as
  required player input, consent and turn order in transition guards; the source
  of a recording event does not bypass them. Response recording cannot request
  another narration pass, and Narratron receipts cannot trigger further actions.
  View buttons may reference only player/contributor actions. The canvas action
  endpoint likewise accepts only player/contributor actions. The story planner
  sees the current state, revision, and Narratron action contracts, and can submit
  only those actions.
  The story responder sees the current game state in its turn prompt, without
  action contracts or package instructions, and cannot mutate it. An optional
  `triggering` boolean (default `true`) controls whether a player or contributor
  action notifies Narratron and queues a planner heartbeat; set `triggering: false`
  for exploratory UI interactions like selection. With `local_preview: true` at
  the package root, these interactions run privately in the browser until a
  triggering action submits the draft. A successful triggering player or
  contributor game action queues a planner heartbeat with both the submitted action
  and an independent snapshot of the newly committed state, so the Narratron side
  can react and narrate without waiting for a separate spoken turn.

The host validates arguments, computes derived values, checks the guard, evaluates
transition bindings and result, validates the candidate state and invariants, and
materializes its view. Only then does it publish and persist the new state. Any
failure leaves the old state, revision and view intact. It never partially applies
an accepted transition's individual fields.

Reset, selection, movement, trading, automatic reactions, turns and game-over
rules are all authored transitions/expressions, not built-in host operations.
Without `local_preview`, every action remains a shared, revisioned transition.

## Local exploration and turn commitment

Set `local_preview: true` to send the state and compiled player rules with the
view. The browser interprets a bounded, data-only CEL tree; package JavaScript
never executes. Narratron instructions, model projections and Narratron-only
transitions are excluded from this client program. The exporter supports integer,
string, boolean, null, list and map expressions used by the bundled games.
Unsupported expressions (including floating-point literals) keep the package on
the existing server-rendered path.

Non-triggering transitions update a private draft without making network calls,
persisting shared state, or notifying StoryRecorder. The UI offers Undo and Discard
changes. A triggering transition such as Trader's Propose trade sends the draft
and final action in one request. Set `preview: true` on a triggering transition
to preview it first and display an explicit End turn button, as checkers does for
movement. After that preview, use Undo to try a different move or End turn to
commit it. Continued checkers jumps retain their existing per-jump commit boundary.

The action request contains `{name, arguments, revision, draft: [{name, arguments}]}`.
The server checks the original revision, every step's actor and contributor
permission, argument schemas, guards, resulting state and invariants, then
publishes the final view. Draft prefixes may contain only non-triggering player
actions. The final action must trigger game control. The entire draft commits
with one revision, persistence operation, durable receipt and StoryRecorder event;
any failed step rolls everything back. The client never submits a replacement
state. Old action requests without a draft remain supported.

Polling the same game revision preserves the draft. A newer revision, different
package, or permission change discards it with a visible explanation. Rejected
submissions discard the draft and refresh the authoritative view. Reloading also
discards private exploration; other viewers see only committed state.

## Rendering language

Nodes support `column`, `row`, `grid`, `text`, `button`, `svg`, `g`, `path`,
`rect`, `circle`, `ellipse`, `line`, `polyline`, `polygon`, `svg_text`, `tspan`,
`defs`, `linearGradient`, `radialGradient`, `stop`, `clipPath`, `mask`, and
`symbol`. Each node has CEL-valued `props` and optional `children`.

An `svg` node can also reference a static `.svg` asset placed in the adventure's
`game/` folder using the `src` (or `href` / `asset`) prop (for example,
`"src": "\"knight.svg\""` or `"src": "\"game/pieces/knight.svg\""`). SVG files in
`game/` are validated against the same tag and attribute allowlist (no scripts,
`foreignObject`, event handlers, or external URLs) and materialized into the
rendered view tree.

`repeat` is a CEL expression yielding a list; `binding` names the current item in
each copy's scope (default `item`). For example, a grid can repeat a cell template
over a package-defined list of coordinates. There is no host board size, cell
coordinate system, piece type or player color.

Supported properties:

- Content/accessibility: `text`, `label`, `id`, `disabled`, `pressed`.
- Layout/style: `columns` (grid), `gap`, `padding`, `color`, `background` (color
  only), `fontSize`, `fontWeight`, `borderRadius`, `outline`, `aspectRatio`,
  `textAlign`.
- SVG: `src` (relative `.svg` file in `game/`), `viewBox`, `preserveAspectRatio`,
  `x`, `y`, `x1`, `y1`, `x2`, `y2`, `dx`, `dy`, `width`, `height`, `cx`, `cy`,
  `r`, `rx`, `ry`, `d`, `points`, `fill`, `fill-opacity`, `fill-rule`, `stroke`,
  `stroke-width`, `stroke-opacity`, `stroke-linecap`, `stroke-linejoin`,
  `stroke-dasharray`, `stroke-dashoffset`, `stroke-miterlimit`, `opacity`,
  `transform`, `text-anchor`, `dominant-baseline`, `font-size`, `font-weight`,
  `font-family`, `letter-spacing`, and gradient/stop attributes.

A button's `action` declares CEL expressions for its `name` and JSON-object
`arguments`. Optional `confirmation` is literal confirmation-dialog text, and
optional `permission` (`"orator"` or `"contributor"`) annotates whether
collaborating contributors may invoke the action.
Disabled buttons are a UI aid; the server always validates the submitted action
and guard independently. The renderer uses text nodes and an allowlist of tags,
attributes and styles. It never evaluates theater JavaScript, inserts theater
HTML, loads theater CSS or evaluates CEL in the browser.

CEL view expressions execute on the server when state changes. The browser gets
the materialized view, not raw state or rules, and can render it repeatedly
without recomputing mechanics. Views are currently the same for every viewer;
authors should not render hidden information or include it in action arguments.

## CEL and execution limits

The runtime uses the CEL interpreter, with no Python compilation or host I/O
extensions. Supported calls are `size`, `string`, `int`, `bool`, author-defined
pure functions, and collection `map`, `filter`, `exists`, `all`, `size` methods.
Arithmetic, conditions, list/map literals and indexing are available. Arbitrary
methods, regular-expression methods, object construction and recursive functions
are rejected.

Limits include:

- Package: 128 KiB (`game/game.json` and each `game/*.svg` asset); CEL expression:
  4,000 characters and 1,000 AST nodes.
- Each transition-evaluation or view-preparation phase: 500,000 visited AST nodes
  and a five-second deadline. Nested macros and author functions share the phase's
  budget. Work exhaustion rejects the operation even if CEL suppresses an error.
- Evaluated JSON values, state and materialized view: 128 KiB each. CEL values are
  copied incrementally with a size budget and a nesting limit of 64, so oversized
  repeated values are rejected before a full JSON expansion is allocated.
- Action: 4 KiB; rendered view: 1,024 nodes, nesting depth 12, repetition 256.
- JSON Schema references and regular expressions are rejected to prevent network
  resolution, recursive references and unbounded regex evaluation.

Limits are host policy, not package-adjustable settings. This is an experimental
format with bounded execution, not a claim of formal rule correctness.

## Persistence and API

`GameState` is independent of `StoryState`. Saved data is keyed by the package's
SHA-256 hash and includes the generic state object and monotonic revision. A
changed package starts from its declared initial state instead of interpreting
an old save using different rules. Format-1 prototype saves are not migrated.

`POST /api/game/action?theater_id=...` accepts:

```json
{"name": "increment", "arguments": {}, "revision": 0}
```

Stale revisions return 409. Illegal actions return 400. Viewer submissions return
403 (unless Collaboration Mode is enabled, the viewer has contributor permission,
and the action is annotated with `"permission": "contributor"`). Successful
actions return the new revision and materialized view and notify canvas
subscribers. Retries with the same revision cannot apply twice.

Game failures are isolated from story and chat loading. Invalid packages expose
a game-only error; invalid saved games retain the current valid game state and
report the restore failure. Transition and rendering failures roll back the
action. Unexpected endpoint failures return a controlled HTTP 500 without
internal exception details. Notification failures are logged without rolling
back an already persisted move; refreshing retrieves the committed revision.
Failed actions also publish a game error to canvas subscribers. The game button
shows an error badge while the panel remains closed until clicked; the panel
shows the message alongside the last valid view. A successful action clears the
error. Internal tracebacks remain in server logs.
These exception boundaries run in the server process, not a separate sandbox
process, so they do not protect against process termination or resource exhaustion.

For the updated example, redeploy `adventures/example_adventure` to load its new
package. A running theater retains its loaded program until its canvas state
manager is recreated; in-place rule hot reload is not implemented.

Run the contract, package and browser checks with:

```powershell
uv run pytest -n 0 components/canvas/game_state_test.py components/game_package_test.py api_server/game_actions_test.py testing/ui/game_renderer_test.py
```
