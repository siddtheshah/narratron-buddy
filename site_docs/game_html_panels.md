# Authored game panels

Games can supply `game/panel.html` beside `game/game.yaml` (or `.yml` / `.json`).
Replace the structured `view` declaration with a `panel` object:

```yaml
panel:
  parameters:
    score: state.score
    status: 'state.score >= 10 ? "Finished" : "Playing"'
    finished: state.score >= 10
  actions:
    add_point:
      name: '"add"'
      arguments: '{"amount": 1}'
```

`parameters` maps names to CEL expressions. Expressions have the same state,
constants, derived bindings, helper functions, and opted-in canvas observation
as structured view properties. Parameters are recomputed after actions and when
canvas observations change. They do not mutate game state; transitions still
own state changes. Up to 24 parameters and 64 named actions are supported.

The matching `panel.html` could be:

```html
<!doctype html>
<html>
<head>
  <style>
    body { margin: 0; color: #eef3fb; font-family: system-ui; }
    .score { font-size: 2rem; }
    button { padding: 0.5rem 1rem; }
  </style>
</head>
<body>
  <p class="score" data-panel-text="score"></p>
  <p data-panel-text="status"></p>
  <button data-panel-action="add_point" data-panel-disabled="finished">Add point</button>
</body>
</html>
```

- `data-panel-text="score"` writes a parameter as text, without interpreting HTML.
  Dot paths select nested map fields or array indices. Lists and maps display as JSON.
- `data-panel-hidden="finished"` hides an element when the parameter is truthy.
- `data-panel-action="add_point"` invokes a named, server-resolved action. Use a
  `<button>` for keyboard accessibility. Missing actions and unauthorized actions
  are disabled. Action arguments come from CEL, never from frame messages.
- `data-panel-disabled="finished"` additionally disables an action button when
  the parameter is truthy.

Actions support the same `confirmation` and `permission` fields as structured
view actions. Server checks of actor roles, arguments, guards, revisions, and
canvas effects remain in place. HTML panels use server evaluation; local browser
previews are disabled. Each redraw recreates the frame, so ephemeral DOM state
such as expanded `<details>` is reset.

## HTML restrictions

Use standard image elements to display the game's SVG assets:

```html
<img src="pieces/token.svg" alt="Player token" width="48" height="48">
```

Paths resolve within `game/`; the optional `game/` prefix is also accepted.
Nested folders and repeated images are supported. Assets use the same SVG
validation as structured views and are embedded as SVG data images, without
network requests. Missing assets, traversal, absolute paths, external URLs,
authored data URLs, `srcset`, and non-SVG images are rejected. Image elements
support `alt`, `width`, `height`, and the usual panel attributes and bindings.
Inline SVG markup remains unsupported. The expanded panel, including embedded
images, is limited to 1 MiB. Changes to referenced SVGs change package identity.

Panels are passive HTML with inline CSS. Standard text, section, list, table,
button, details, progress, and meter elements are supported. The loader rejects
scripts, event handlers, links, forms, inline SVG, embeds, other resource attributes,
and authored metadata. The file must be UTF-8, at most 128 KiB, and contain at
most 2,048 elements. A declared panel requires its HTML file at runtime and on
theater import. ZIP and folder imports preserve `game/panel.html`.

The browser renders the validated HTML in a sandboxed iframe with an opaque
origin. Its content security policy permits data images while blocking other
resource types, network connections, base URL changes, and form submissions.
Inline CSS is allowed; CSS imports, fonts, and remote background images cannot
load resources. Game authors can control panel sizing by setting `width` / `max-width` / `--panel-width` and `height` / `min-height` / `--panel-height` on `:root`, `html`, or `body` in `panel.html`; the host clamps width to 280px–960px (and viewport bounds) and height to 220px–780px (defaulting to 510px × 430px). Only a host-generated,
nonce-authorized binding/action bridge executes JavaScript. Authored JavaScript
is unsupported. Panel parameters are JSON data and are never interpolated as
markup. Changes to panel HTML change the package identity, invalidating saved
state from the previous package just as a changed game declaration does.

## Frontend review

The existing flow is a CEL-evaluated `ViewNode` tree, serialized by Python or
CEL-Go and converted to DOM by `frontend/canvas/game-renderer.ts` (served as
`/static/generated/canvas/game-renderer.js`). View actions also
participate in contributor authorization; a separate HTML action endpoint would
duplicate this behavior. The panel contract therefore compiles into an internal
view containing parameter values and named actions. Both runtimes retain their
evaluation budgets and action checks. The renderer passes those values into the
isolated HTML frame through the trusted bridge. Existing structured views and
canvas APIs remain available.
