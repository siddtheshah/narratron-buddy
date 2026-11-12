# Canvas TypeScript migration coverage

The core baseline is commit `b5632e9`, before the TypeScript migration:
17 Python files, 104 test definitions before parametrization. Two definitions
exercise Python backend state/handlers and remain in Python. The other 102 are
frontend migration candidates.

This inventory counts original definitions, not current Playwright tests. Several
TypeScript tests group related behaviors; parametrized cases must also survive.
Keep each Python suite until its replacement passes with the same permissions,
persistence, geometry, and race-condition checks.

| Original file in `testing/ui/` | Baseline definitions | Current coverage |
| --- | ---: | --- |
| `a2ui_canvas_drag_test.py` | 7 | Python; pending migration |
| `canvas_activity_test.py` | 5 | `agent-activity_test.ts`; timeline stability across refreshes, partial updates, dual visual-tool completion, 30s stale lease, and 10m hard timeout |
| `canvas_bookmarks_test.py` | 1 | `bookmarks_test.ts`; includes persistence, role visibility, navigation, failed jumps |
| `canvas_chat_name_test.py` | 5 | Python; pending migration |
| `canvas_collaboration_bar_test.py` | 1 | `collab-bar_test.ts`; viewer bar visibility, collaboration status, modal layout, and role toggling at 1000px and 1400px |
| `canvas_corner_controls_test.py` | 1 | `corner-controls_test.ts`; all 3 corners, 7 visibility masks, edge packing, and expanded panel overlap |
| `canvas_geometry_test.py` | 1 | `renderers_test.ts`; 2 fit modes, each with 4 viewport/zoom combinations at DPR=2 |
| `canvas_navigation_test.py` | 1 | `navigation_test.ts`; pan, zoom, pinch, coordinate conversion, reset, local state |
| `canvas_pin_test.py` | 1 | `pin-control_test.ts`; all 4 pin combinations, API toggling, viewer visibility, and error handling |
| `canvas_resize_test.py` | 1 | `layout-resize_test.ts`; layout-resizing cover across reversed slides, settle callbacks, and viewport resizes |
| `canvas_scenes_characters_test.py` | 23 | Python; pending migration |
| `canvas_segment_select_test.py` | 13 | `doodle-renderer_test.ts`; 18 original cases replaced by 20 browser cases, preserving 6 zoom/pan/reverse combinations and adding permission/history checks |
| `canvas_speech_replay_test.py` | 2 | Python; pending migration |
| `canvas_stamp_test.py` | 21 | Python; pending migration |
| `canvas_text_edit_test.py` | 3 | `text-annotations_test.ts`; selection, inline editing, Escape cancellation, drag previews, pointer cancellation, replay, and permission checks |
| `canvas_undo_test.py` | 17 | 15 frontend definitions replaced by `doodle-actions_test.ts`; 2 backend tests retained in Python |
| `canvas_zoom_effect_test.py` | 1 | `renderers_test.ts`; 4 fit/source-size cases, each with 5 zoom/resize combinations at DPR=2 |

Current disposition: 44 original frontend definitions have TypeScript replacements,
58 frontend definitions remain in Python, and 2 backend definitions remain in Python.
The implementation migration is still in progress; image and doodle rendering are
TypeScript, while the remaining canvas controller logic is JavaScript. Legacy Python
renderer snippet fixtures read `static/generated/canvas/doodle-renderer.js` until
their behavioral coverage moves to TypeScript.

Additional coverage outside this baseline:

- `cursor-state_test.ts`: store validation, subscription, interoperability, and real canvas controls.
- `state-polling_test.ts`: all 12 original CJS cases plus module-shim compatibility.
- `image-renderer_test.ts`: CDN policy, shim identity, sequence pixels and lifecycle,
  layered composition/effects, silent video fallback and looping, and load cancellation.
- `media_test.ts`: manifest signatures, URL resolution, timing defaults, option forwarding,
  input immutability, and legacy shim identity.
- `testing/ui/canvas_fixtures_test.ts`: asset serving, request isolation, service-worker
  blocking, secondary contexts, and full canvas mounting.
- `testing/ui/triframe_animation_test.py` and `video_animation_ui_test.py` retain
  backend manifest/path/history checks.
- Story-log inspector and audio-recorder CJS suites remain pending migration.

Verification: `npm test` runs strict type checking, the production build, all
TypeScript Playwright tests, and remaining CJS tests. Run Python checks with
`uv run pytest`; generated modules are built by the UI fixture when stale.
