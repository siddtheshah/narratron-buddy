# Theater game packages

`game/game.yaml` declares a game's state, transitions and view. Narratron has no
built-in game rules or game-specific renderer. The example adventure contains a
checkers implementation; `testing/game_fixtures.py` declares an unrelated counter
using the same runtime and renderer.

The entry point is mounted by adventure selection, folder import and ZIP import.
The canvas game button appears when a package is present and opens only on click.
The active orator can submit actions; when the active orator enables collaboration
mode, viewers with contributor permission can also submit actions annotated with
`"permission": "contributor"`. Other viewers see the committed view.
A live story agent is not required.

## Interaction model: turn-based transactional games

Narratron supports **turn-based transactional** games. Play advances through
discrete, validated changes to shared state, with stable points where the player
and Narratron can inspect the result and decide what to do next. Narratron does
not support real-time game environments: mechanics must tolerate either side
taking time to respond, without requiring continuous input, physics updates or
precisely timed reactions.

The main game transactions must either require acceptance from both the player
and Narratron, or strongly constrain what each can do independently before the
other gets to act. This can take two forms:

- **Mutual acceptance:** one side proposes specific terms, and the other accepts or rejects them. For a trade, record the pending offer, then exchange goods and payment together only when the recipient accepts those exact terms. Changing the terms requires a new acceptance. Rejection or cancellation clears the offer without completing the exchange. Apply the same rule to Narratron's offers to the player.
- **Bounded turns:** agreed rules authorize a limited action or sequence, then hand control to the other side. In checkers, a legal move (including a required chain of jumps) ends the side's turn. Neither side may keep taking unrelated moves while waiting for the other. Individual moves need no separate approval from the opponent because the turn rules already constrain them.

An accepted game transaction can span several runtime transitions: proposing an
offer commits pending state; accepting it later commits the exchange. Each
transition is atomic, but runtime validation of a proposal is **not** Narratron's
acceptance. Authors must encode pending terms, acceptance, turn ownership and
action limits in state and transition guards. Narration, disabled buttons and
planner instructions alone do not enforce these rules. The host does not add a
universal approval handshake or alternating-turn rule to packages.

### Conceptual tests for a game idea

Use these questions before designing a package:

1. **Can both sides wait?** If the player pauses or Narratron takes a while to respond, can play remain at a valid decision point without missed inputs, collisions or deadlines changing the outcome? If continuous timing is essential, the game does not fit.
2. **Can an action settle as a discrete result?** Can a move, purchase, trade or production step be described by explicit inputs and a complete resulting state? It must resolve within the runtime's execution limits, without an ongoing simulation loop.
3. **What stops either side acting alone indefinitely?** Identify the required acceptance, turn boundary or finite action budget. Test both directions: the player must not bypass Narratron's decision, and Narratron must not bypass the player's. A stream of individually valid clicks is insufficient if it lets one side advance the main game without that boundary.
4. **Can the rules enforce the boundary?** Out-of-turn moves, repeated acceptance and acceptance of changed terms must fail through guards and state checks. Local selection and previews may explore choices, but must not complete a shared outcome before its required acceptance or turn commitment.

- **Checkers — fits.** Moves have discrete outcomes, legal moves are constrained, and turns pass between the player and Narratron. The board can wait for either side.
- **Brickbreaker — does not fit.** The moving ball and paddle require continuous simulation and timely player input. Waiting changes whether the player hits the ball.
- **Cookie Clicker-style progression — fits with bounded transactions.** Clicks, purchases and production can become discrete actions or finite batches. Require a handoff or mutual acceptance at the main progression boundaries; resolve production as explicit steps. Continuous background ticking and unlimited progression while Narratron waits do not fit this model.
- **Angry Birds — does not fit in its usual physics-based form.** Choosing a shot is discrete, but resolving flight, collisions and collapsing structures depends on a continuous physics simulation. A bounded rules-based shot outcome would be a redesigned transactional game.

Selection, previews and other small interactions do not each need a Narratron
response. They must stay within the current turn's constraints. For example, a
player can adjust an offer privately before submitting it, but cannot use those
adjustments to transfer goods before the other side accepts.

## Minimal package

Use YAML for human-authored packages: comments explain intent, and `|-` lets CEL
expressions span lines without escaping their quotes. `game/game.yml` and the
existing `game/game.json` format are also supported everywhere, including imports,
the theater editor, Adventure Runner and Game Testing Lab. Keep exactly one game
declaration in the `game/` folder; multiple declarations are rejected.

```yaml
version: 2
initial:
  count: 0
state_schema:
  type: object
  properties:
    count: {type: integer, minimum: 0, maximum: 10}
  required: [count]
  additionalProperties: false

transitions:
  increment:
    permission: contributor
    parameters: {type: object, additionalProperties: false}
    guard: state.count < 10
    # Return the complete next state.
    result: |-
      {"count": state.count + 1}

view:
  kind: column
  children:
    - kind: text
      props:
        text: '"Count: " + string(state.count)'
    - kind: button
      props:
        text: '"Add one"'
        disabled: state.count >= 10
      action:
        name: '"increment"'
        arguments: '{}'
```

All values in `props`, and the `name` and `arguments` of a view action, are CEL
expression **strings**. Literal text therefore keeps its CEL quotes, as in
`text: '"Add one"'`. Quote numeric and boolean CEL expressions too: `columns: '8'`
or `disabled: 'false'`. State data uses ordinary YAML values such as `count: 0`
and `draw: false`. For expressions containing `: `, `#`, or leading punctuation,
use a quoted string or `|-` to avoid YAML syntax ambiguity.

The YAML loader accepts one document with string mapping keys and JSON-compatible
values. Duplicate keys, aliases, custom executable tags and non-finite numbers
are rejected. Only lowercase `true` and `false` implicitly become booleans;
words like `yes`, `no`, `on`, `off`, and dates remain strings. YAML goes through
the same schemas, CEL validation and execution limits as JSON. The bundled
`adventures/example_adventure/game/game.yaml` is a commented checkers example.

## State and transitions

- `initial`: an arbitrary JSON object. There are no reserved game field names.
- `instructions`: optional game-specific guidance shown to the story planner with the current game state. Explain when the Narratron side should use its actions and which projected fields are authoritative.
- `model_state`: optional CEL expression that projects the complete game state into a concise JSON value for story modules. The full state remains persisted and validated; this projection is limited to 16 KiB. If omitted, story modules see the full state.
- `state_schema`: JSON Schema for every initial, restored and resulting state.
- `constants`: optional immutable JSON data available to expressions.
- `functions`: ordered named CEL expressions with `parameters` and `expr`. A function may call earlier functions, but not itself or later functions. It receives its parameters and `constants`; it has no implicit access to state.
- `derived`: ordered `{ "name": "...", "expr": "..." }` bindings, evaluated from `state`, `initial` and `constants`. These values are not persisted.
- `invariants`: CEL boolean expressions checked against state and derived values.
- `transitions`: author-named actions. Each declares a `parameters` JSON Schema, optional boolean `guard`, optional ordered `bindings`, and a `result` expression returning the **complete next state object**. Action arguments are available as `action`. Bindings cannot overwrite names already in scope.
- Each transition can set `actor` to `"player"`, `"contributor"`, or `"narratron"`, `permission` to `"orator"` (default) or `"contributor"`, and include a short `description`. Setting `permission: "contributor"` (or `actor: "contributor"`) allows viewers with contributor permission to invoke the action on the canvas whenever the active orator has Collaboration Mode enabled. A button's `action` in `view` can also set `permission: "contributor"` to annotate a specific view action. An optional state-only `available` CEL expression controls whether a Narratron action appears in the planner's current action list. The transition's `guard` remains the authoritative check when an action is submitted. StoryRecorder can execute Narratron actions while recording either a completed story response or a committed player game action. Encode prerequisites such as required player input, consent and turn order in transition guards; the source of a recording event does not bypass them. Response recording cannot request another narration pass, and Narratron receipts cannot trigger further actions. View buttons may reference only player/contributor actions. The canvas action endpoint likewise accepts only player/contributor actions. The story planner sees the current state, revision, and Narratron action contracts, and can submit only those actions. The story responder sees the current game state in its turn prompt, without action contracts or package instructions, and cannot mutate it. An optional `triggering` boolean (default `true`) controls whether a player or contributor action notifies Narratron and queues a planner heartbeat; set `triggering: false` for exploratory UI interactions like selection. With `local_preview: true` at the package root, these interactions run privately in the browser until a triggering action submits the draft. A successful triggering player or contributor game action queues a planner heartbeat with both the submitted action and an independent snapshot of the newly committed state, so the Narratron side can react and narrate without waiting for a separate spoken turn.

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
to preview it first and display an explicit Submit button, as checkers does for
movement. After that preview, use Undo to try a different move or Submit to
commit it. Submit is orange when a move is ready to commit; selecting a piece
alone leaves it disabled. Continued checkers jumps retain their existing
per-jump commit boundary.

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

Nodes support `column`, `row`, `grid`, `tabs`, `text`, `button`, `svg`, `g`, `path`,
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
- Layout/style: `columns` (grid), `gap`, `padding`, `color`, `background` (color only), `fontSize`, `fontWeight`, `borderRadius`, `outline`, `aspectRatio`, `textAlign`.
- A row with `layout: '"sidebar"'` places its first child on the left and its second child on the right in a wider panel. It stacks the children on narrow screens.
- A `tabs` node displays its children as panels, using each child's `label` prop
  as the tab title. The first tab opens by default. Selection stays local to the
  viewer, survives game updates, and works for read-only viewers. Arrow keys,
  Home and End navigate tabs. Give the tabs node a stable `id` if its position
  in the view can change; changing the game package resets the selection.
- SVG: `src` (relative `.svg` file in `game/`), `viewBox`, `preserveAspectRatio`, `x`, `y`, `x1`, `y1`, `x2`, `y2`, `dx`, `dy`, `width`, `height`, `cx`, `cy`, `r`, `rx`, `ry`, `d`, `points`, `fill`, `fill-opacity`, `fill-rule`, `stroke`, `stroke-width`, `stroke-opacity`, `stroke-linecap`, `stroke-linejoin`, `stroke-dasharray`, `stroke-dashoffset`, `stroke-miterlimit`, `opacity`, `transform`, `text-anchor`, `dominant-baseline`, `font-size`, `font-weight`, `font-family`, `letter-spacing`, and gradient/stop attributes.

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
extensions. Supported calls are `size`, `string`, `int`, `bool`, `random_int`,
author-defined functions, and collection `map`, `filter`, `exists`, `all`, `size` methods.
Arithmetic, conditions, list/map literals and indexing are available. Arbitrary
methods, regular-expression methods, object construction and recursive functions
are rejected.

`random_int(min, max)` returns a uniformly random integer with **both bounds
included**. For example, `random_int(1, 6)` rolls a six-sided die and
`random_int(0, 1)` flips a coin. Both arguments must be CEL integers, with
`min <= max`; equal bounds return that value. Invalid arguments fail evaluation.
Each call draws afresh, including calls inside author-defined functions and
collection macros; expressions containing random calls are not memoized.

Roll in a transition binding and store the result in state when it must be
reused or survive save/restore. For example:

```yaml
bindings:
  - name: roll
    expr: 'random_int(1, 6)'
result: '{"last_roll": roll, "total": state.total + roll}'
```

This draws once and uses the same roll for both fields. Direct random calls in
derived values, guards, invariants or views draw again whenever evaluated; keep
those expressions deterministic by reading the stored state instead. Packages
whose exported browser rules contain randomness fall back to server rendering,
even with `local_preview: true`, so a preview cannot roll a different outcome
from the committed action.

Limits include:

- Package: 128 KiB (the YAML or JSON declaration, its decoded JSON, and each `game/*.svg` asset); CEL expression: 4,000 characters and 1,000 AST nodes.
- Each transition-evaluation or view-preparation phase: 500,000 visited AST nodes and a five-second deadline. Nested macros and author functions share the phase's budget. Work exhaustion rejects the operation even if CEL suppresses an error.
- Evaluated JSON values, state and materialized view: 128 KiB each. CEL values are copied incrementally with a size budget and a nesting limit of 64, so oversized repeated values are rejected before a full JSON expansion is allocated.
- Action: 4 KiB; rendered view: 1,024 nodes, nesting depth 12, repetition 256.
- JSON Schema references and regular expressions are rejected to prevent network resolution, recursive references and unbounded regex evaluation.

Limits are host policy, not package-adjustable settings. This is an experimental
format with bounded execution, not a claim of formal rule correctness.

## Persistence and API

`GameState` is independent of `StoryState`. Saved data is keyed by the source
file's SHA-256 hash and includes the generic state object and monotonic revision.
A changed package starts from its declared initial state instead of interpreting
an old save using different rules. Format-1 prototype saves are not migrated.
Converting JSON to YAML or changing comments also changes the source hash and
starts a fresh game when the package is next loaded.

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
