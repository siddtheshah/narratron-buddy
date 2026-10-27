# Narratron Task List

## Game Engine Upgrades
- [x] Raise `game.json` package size limit (`MAX_GAME_BYTES`) from 64 KiB to 128 KiB (131,072 bytes).
- [x] Support loading, validating, packaging, and rendering SVG graphics placed in an adventure's `game/` folder.
- [x] Allow game actions (`transitions` / `action` in `game.json`) to be annotated with `'contributor'` permission.
- [x] Enable viewers with contributor permissions to take `'contributor'`-annotated game actions on the canvas when the active orator has collaboration enabled, triggering Narratron narration as usual.

## Game Performance Investigations

Investigate both tracks independently and together. The native-runtime direction is persistent workers with parent-enforced wall-clock timeouts; the evaluator and production integration remain to be selected. Interpreter step counts were a proxy for elapsed time and do not need to be reproduced. Keep the optimized `example_adventure` as the baseline; the experimental 2-D board refactor was rolled back because it did not improve performance.

### 1. Native CEL runtime

- [x] Prototype a native alternative to the pure-Python `cel-python` evaluator. C++ and Rust bindings were evaluated and retired after the CEL-Go comparison; their experiment modules, dependencies and Docker options have been removed. Historical transaction measurements are retained in the [CEL-Go analysis](analysis/cel_go_worker_benchmark.md).
- [x] Compare an in-process native extension with a persistent CEL-Go worker if process isolation or resource-limit support favors a separate binary. Cache compiled programs and batch whole operations; avoid starting a process for every expression.
  - Retained offline CEL-Go v0.33.0 worker and Python/Go transaction harness: `game/cel_go_worker/`, `game/go_cel.py`, `testlab/benchmark_go_cel.py`, with the shared workload in `testlab/game_benchmark.py`. Seven-round checkers outputs matched, including browser payloads and rollback. Player turn median was about 22 ms for Go versus 94 ms for C++ and 879 ms for Python in the historical comparison; [results, reproduction and remaining boundaries](analysis/cel_go_worker_benchmark.md). Parent timeout/kill/replacement is tested. OS memory containment, recovery/contributor integration and broader semantic compatibility remain open; `string(2.0)` formatting differs.
- [ ] Verify compatibility for package-defined functions, `random_int`, null comparisons, error masking, integer behavior, state transitions, and browser previews.
- [ ] Enforce wall-clock deadlines from a parent process across complete worker operations, including nested expressions, custom functions and conversion. Kill and replace timed-out workers, discard partial results, and keep persistence in the parent. Retain input/output size limits and add a worker memory boundary for native intermediate allocations; CEL termination does not imply small memory use. Matching the old interpreter step counter is not required.
- [ ] Benchmark complete game loading, player submissions with selection drafts, Narratron replies, and expensive/error cases. Measure Python/native conversion and callback overhead as well as CEL evaluation.

References: [CEL-Go evaluation and cancellation](https://pkg.go.dev/cel.dev/cel-go/cel).

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

