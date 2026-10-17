# Theater game packages

`game/game.json` declares a game's state, transitions and view. Narratron has no
built-in game rules or game-specific renderer. The example adventure contains a
checkers implementation; `testing/game_fixtures.py` declares an unrelated counter
using the same runtime and renderer.

The entry point is mounted by adventure selection, folder import and ZIP import.
The canvas game button appears when a package is present and opens only on click.
The active orator can submit actions; other viewers see the committed view.
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

The host validates arguments, computes derived values, checks the guard, evaluates
transition bindings and result, validates the candidate state and invariants, and
materializes its view. Only then does it publish and persist the new state. Any
failure leaves the old state, revision and view intact. It never partially applies
an accepted transition's individual fields.

Reset, selection, movement, trading, automatic reactions, turns and game-over
rules are all authored transitions/expressions, not built-in host operations.
The example's `select` is a real transition: selection is shared by viewers and
increments the state revision. Local-only UI selection is not implemented yet.

## Rendering language

Nodes support `column`, `row`, `grid`, `text`, `button`, `svg`, `rect`, `circle`
and `svg_text`. Each node has CEL-valued `props` and optional `children`.

`repeat` is a CEL expression yielding a list; `binding` names the current item in
each copy's scope (default `item`). For example, a grid can repeat a cell template
over a package-defined list of coordinates. There is no host board size, cell
coordinate system, piece type or player color.

Supported properties:

- Content/accessibility: `text`, `label`, `id`, `disabled`, `pressed`.
- Layout/style: `columns` (grid), `gap`, `padding`, `color`, `background` (color
  only), `fontSize`, `fontWeight`, `borderRadius`, `outline`, `aspectRatio`,
  `textAlign`.
- SVG: `viewBox`, `x`, `y`, `width`, `height`, `cx`, `cy`, `r`, `rx`, `fill`,
  `stroke`, `stroke-width`, `text-anchor`, `dominant-baseline`.

A button's `action` declares CEL expressions for its `name` and JSON-object
`arguments`. Optional `confirmation` is literal confirmation-dialog text.
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

- Package: 64 KiB; CEL expression: 4,000 characters and 1,000 AST nodes.
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
403. Successful actions return the new revision and materialized view and notify
canvas subscribers. Retries with the same revision cannot apply twice.

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
