# Narratron Task List

## Game Engine Upgrades
- [x] Raise `game.json` package size limit (`MAX_GAME_BYTES`) from 64 KiB to 128 KiB (131,072 bytes).
- [x] Support loading, validating, packaging, and rendering SVG graphics placed in an adventure's `game/` folder.
- [x] Allow game actions (`transitions` / `action` in `game.json`) to be annotated with `'contributor'` permission.
- [x] Enable viewers with contributor permissions to take `'contributor'`-annotated game actions on the canvas when the active orator has collaboration enabled, triggering Narratron narration as usual.

## Game Performance Investigations

Investigate both tracks independently and together. No architecture or runtime migration has been selected. Keep the optimized `example_adventure` as the baseline; the experimental 2-D board refactor was rolled back because it did not improve performance.

### 1. Native CEL runtime

- [ ] Prototype replacing the pure-Python `cel-python` evaluator with a native implementation, starting with the C++ `cel-expr-python` bindings.
- [ ] Compare an in-process native extension with a persistent CEL-Go worker if process isolation or resource-limit support favors a separate binary. Cache compiled programs and batch whole operations; avoid starting a process for every expression.
- [ ] Verify compatibility for package-defined functions, `random_int`, null comparisons, error masking, integer behavior, state transitions, and browser previews.
- [ ] Preserve work budgets and deadlines across nested expressions and custom functions, plus existing input, intermediate-value, and output size limits. Confirm the binding exposes the required controls before adopting it.
- [ ] Benchmark complete game loading, player submissions with selection drafts, Narratron replies, and expensive/error cases. Measure Python/native conversion and callback overhead as well as CEL evaluation.

Initial local expression benchmark: `cel-python` 0.4.0 versus `cel-expr-python` 0.1.3 on Windows / CPython 3.13.14. Expressions were compiled once; evaluation timings include conversion to ordinary Python results. Seven timing rounds produced these medians, with matching results:

| Checkers expression | Python | Native C++ | Speedup |
| --- | ---: | ---: | ---: |
| Candidate moves | 51.80 ms | 0.55 ms | 93x |
| Board invariant | 28.49 ms | 0.32 ms | 89x |
| Piece counts | 18.96 ms | 0.42 ms | 45x |

These are expression-level results, not full-transaction speedups. The C++ binding loaded successfully from a prebuilt Windows wheel in an isolated `uv` environment; project dependencies were not changed. A native CEL runtime still evaluates the restricted expression language; it does not require allowing arbitrary user-authored JavaScript or native executables.

References: [C++ Python bindings](https://github.com/cel-expr/cel-python), [wheel availability](https://pypi.org/project/cel-expr-python/), [CEL-Go cost limits and cancellable evaluation](https://pkg.go.dev/cel.dev/cel-go/cel).

### 2. Client-side declarative UI rendering

- [ ] Investigate keeping CEL on the server for game rules/state while application-owned JavaScript renders declarative UI templates in the browser.
- [ ] Keep author content data-only: allowed components, bindings, conditions, and repetition. Do not require arbitrary author JavaScript, event handlers, or unrestricted HTML.
- [ ] Compare moving the existing bounded UI expression interpreter entirely to the browser with a simpler UI binding language that does not use CEL syntax.
- [ ] Define a state/action contract: send public state, revision, and public derived facts such as legal moves; accept `{name, arguments, revision}` actions. Keep guards, permissions, and validation authoritative on the server, including authorization currently derived from rendered views.
- [ ] Keep presentation state such as selection, hover, and layout local where possible. Decide how previews, Submit, Undo, and Discard work without duplicating authoritative game rules.
- [ ] Measure complete browser interactions, server CPU, update payload sizes, and behavior on lower-powered clients. Preserve bounded client work and safe DOM construction.

Initial local simulation removed CEL view compilation/rendering, browser-program export, and UI-only derived bindings. Game state and Narratron snapshots matched. Three-run server-time medians against the optimized example were:

| Operation | Current | Without CEL views | Reduction |
| --- | ---: | ---: | ---: |
| Load game | 3.70 s | 1.96 s | 47% |
| Submit player move, including selection draft | 5.05 s | 3.24 s | 36% |
| Apply Narratron move | 3.64 s | 2.02 s | 45% |

These measurements exclude network/AI latency and the cost of a replacement frontend, which has not been implemented. Evaluation deadlines were relaxed to avoid timing-dependent recovery retries during comparison. Existing browser CEL previews measured approximately 11–14 ms, so the principal demonstrated saving is server-side evaluation. Current updates are about 93 KB, including about 53 KB of rendered view; state plus revision alone is roughly 350 bytes before derived facts and metadata.

### Evaluation and decision

- [ ] Add a reproducible benchmark harness for both tracks, including cold load, steady-state turns, output parity, resource exhaustion, and payload sizes.
- [ ] Compare native CEL alone, client-side rendering alone, and their combination before choosing a migration. Require measurable end-to-end improvement and preservation of the restricted authoring/security model.

## Auth, Security & Monetization
- [ ] Add rate limits for registration/login, password reset, join-key resolution, uploads, payment attempts, and Live WebSocket connections.
- [ ] Use secure production auth cookies and CSRF protection for authenticated state-changing endpoints.
- [ ] Require authenticated theater ownership (or the active baton holder where appropriate) for agent start, stop, and status endpoints.
  - The current lifecycle endpoints accept a theater ID without checking the caller's identity or authorization.
- [ ] Cloudflare

## Quality Testing
- [ ] Test Deepseek V4.1-flash as an alternative to gemini.
   - Can use LiteLLM as a wrapper to get compatibility with ADK agents in story modules.


## Refactors
- [ ] Eliminate traces of 'named element' terminology.
- [ ] Refactor canvas.html to be more modular.

## Billing
- [ ] Storage Daemon is not checking file sizes of owned theaters. Need to fix.

