# Persistent CEL-Go worker benchmark

The offline prototype in `game/cel_go_worker/` evaluates complete game
operations in Go. It is a promising performance candidate, with matching
checkers outputs, but is not a production runtime selection. No production
imports, dependency groups, Docker images or runtime flags were changed for it.

## Reproduction

Use Go 1.23 or newer. Dependencies are pinned in `go.mod` and `go.sum`, using
the canonical `cel.dev/cel-go` module. Build from the worker directory:

```powershell
New-Item -ItemType Directory -Force scratch | Out-Null
Push-Location game/cel_go_worker
go build -o ../../scratch/cel-go-worker.exe .
go vet ./...
Pop-Location

uv run pytest game/go_cel_test.py -n 0
uv run python -m testlab.benchmark_go_cel --rounds 7 --output testlab/benchmark_output/cel-go-python-windows.json
```

On Linux/macOS build `../../scratch/cel-go-worker` instead. Both executables
live in the ignored `scratch/` directory. For another location, pass `--worker`
to the benchmark and set `NARRATRON_CEL_GO_WORKER` for pytest. Tests skip when
the executable is absent; pytest does not download dependencies or compile Go.

The test machine initially had Go 1.19.1. A checksum-verified official Go 1.27.3
archive was extracted under `scratch/cel-go-toolchain/` for these runs; the
system Go installation was unchanged. Reproduction commands above assume a
sufficiently recent Go on PATH. With the local toolchain, use
`../../scratch/cel-go-toolchain/go/bin/go.exe` from the worker directory.

## Boundary and measurement

`GoWorker` uses a persistent subprocess and newline-delimited JSON over stdio.
There is one package per worker, replaced on load, and one in-flight operation
with no waiting queue. Programs are parsed/planned once per package. The worker
implements package-defined helpers, `random_int`, derived bindings, transition
guards/results, invariants, repeated declarative views, SVG expansion and
model-facing facts/action availability. Helpers stay inside Go. Deterministic
expression results are cached within an operation; random helpers and their
callers bypass that cache using the parent's existing dependency analysis.

One action request includes every selection draft step, the committed move,
and final view/model preparation. Intermediate state proposals are returned for
parent schema validation. Python retains the existing author restrictions,
JSON schemas, browser program export, actor/revision checks, story receipts,
persistence serialization and commit/rollback path. The benchmark adapter
subclasses `GameState` only in `testlab`; the application still uses its current
bounded Python evaluator.

The harness uses `testlab.game_benchmark.sample` with the optimized,
unchanged `example_adventure`. It compares full payloads (including browser
programs and SVGs), story snapshots, saved state and revisions after load,
player selection draft + move, Narratron reply and invalid move/rollback.
Persistence writes a temporary JSON file. There are no AI or network calls.
Move selection and parity comparisons occur outside timed actions.

Each Go sample starts a new worker, then reuses it for package load and all
actions. Process launch + readiness is reported separately from package load.
Python imports are excluded for all backends. Rounds rotate backend order in
the same Python process; package-load medians benefit from Python parser/cache
warmup and are not fresh-Python-process cold-start measurements. The JSON report
includes raw operation timings, startup times and Go profiles for every round,
plus the Go/CEL-Go versions read from the actual binary.

`rpc_ms` includes parent request serialization, transport, worker execution,
worker response serialization and parent response decoding. `operation_ms` is
worker request decoding/normalization through operation completion, including
compilation during load, but excludes final response serialization.
`conversion_ms` is the subset spent copying CEL results and helper arguments
and results into bounded JSON values. Lazy input adaptation, interpreter work,
helper dispatch and rendering are still combined in the remainder; this is not
an isolated callback-versus-interpreter microbenchmark. Counters report actual
evaluations, helper calls and cache hits. These metrics are instrumentation and
their overhead is included in the result.

Windows worker instrumentation uses `QueryPerformanceCounter`, matching the
high-resolution clock underlying Python's `perf_counter`. Go's Windows
`time.Now` interrupt-time clock was too coarse for short conversion spans.
Other platforms use Go's monotonic `time.Since` clock.

## Local results

Historical seven-round comparison on Windows 11 x64, CPython 3.13.14, Go 1.27.3,
CEL-Go v0.33.0, `cel-python` 0.4.0, C++ `cel-expr-python` 0.1.3 and
Rust `common-expression-language` 0.10.0:

The C++ and Rust experiment modules, tests, dependency groups and Docker build
options have since been removed. Their measurements are retained here as
historical comparison data. The current harness compares Python and Go only.

| Complete operation | Python | C++ in process | Rust in process | Go worker |
| --- | ---: | ---: | ---: | ---: |
| Package load | 648.66 ms | 290.45 ms | 430.62 ms | 246.64 ms |
| Player move including selection draft | 878.69 ms | 93.55 ms | 237.99 ms | 21.99 ms |
| Narratron reply | 654.36 ms | 82.82 ms | 212.68 ms | 20.54 ms |
| Invalid move / rollback | 93.67 ms | 7.44 ms | 15.28 ms | 3.19 ms |

The worker startup/readiness median was 24.56 ms, separate from package load.
All seven rounds matched; final update payloads were 93,057 JSON bytes. The
player operation was about 40x faster than Python and 4.3x faster than the C++
prototype in this run. This compares complete implementations, including
different callback/conversion paths and cache scopes, rather than isolating
the speed of the language engines themselves.

Player-operation profile medians were 10.19 ms for the RPC and 7.70 ms inside
the worker, including 3.00 ms of bounded JSON conversion, 891 evaluations,
124 package-helper calls and 953 result-cache hits. The rest of the parent
operation includes proposal validation, view-model decoding, receipts, payload
construction and temporary persistence. Independently computed medians should
not be added or subtracted as exact per-request attribution.

The historical raw report is stored locally at the ignored
`testlab/benchmark_output/cel-go-windows.json`; the reproduction command writes
a separate Python/Go report. Results depend on system load,
Python cache state, antivirus/process startup and filesystem behavior. These
are local trusted-fixture measurements, not deployment latency predictions.

## Compatibility and remaining work

The Go probes cover null/map/list comparisons, missing values and CEL error
masking (including nested helper errors), signed division/remainder, int64
boundaries and overflow, uint callback behavior, random-bound validation,
nested functions, random-cache exclusion, output limits, counter/exchange
transactions, checkers/browser payload parity, and rollback. Direct
`random_int(1u, 6u)` is rejected. Passing an unsigned value through a package
helper intentionally crosses the same signed JSON integer boundary as the
Python implementation.

One known semantic difference is pinned by a probe: CEL-Go returns `"2"` for
`string(2.0)` while the current evaluator returns `"2.0"`. An integral JSON
double remains a double on input and is correctly rejected by `random_int`.
Broader language parity needs additional coverage and a deliberate compatibility
policy for number-to-string formatting.

The parent enforces a five-second deadline per worker request, including
request encoding, stdio writes/reads, all nested worker work, worker result
conversion/view preparation, response encoding and parent wire decoding. It
kills and reaps a timed-out worker, discards its proposal and does not retry.
The Go operation also shares one cooperative `ContextEval` deadline with an
interrupt check frequency of 100 across nested expressions/helpers. Tests use a
bounded-memory expensive comprehension and independently stalled stdio to
verify forced termination, unchanged state/revision, no persistence callback,
and explicit replacement/reload. Parent argument and candidate schema checks
also reject invalid input/intermediate draft states before commit.

These are incomplete production boundaries:

- There is **no worker-specific OS memory limit**. Only trusted, bounded fixtures
  were run; no allocation-explosion stress test was attempted. A Go GC memory
  target would not replace an OS/container limit. Validate a Windows Job Object
  or a separate Linux cgroup/container boundary before untrusted execution.
- Parent proposal/model validation after wire decoding is outside the request
  timer. Production integration must establish an end-to-end operation deadline
  and revision validation at commit across asynchronous worker dispatch.
- Declared `on_error` recovery is rejected and contributor actions are excluded
  by the adapter. The existing recovery/deadline and rendered-view permission
  policies still need implementation and parity tests.
- Input/output frames are capped at 4 MiB, bounded JSON values at 128 KiB,
  view repetition at 256 and dynamic rendered nodes at 1,024. Python checks
  candidate state, model facts, actions and view sizes. The offline adapter
  conservatively counts expanded SVGs toward the view limit, unlike production.
  These accepted-result limits do not bound native intermediate allocations.
- Worker replacement/reload is explicit; there is no production pool, crash
  supervisor or concurrency integration. Client-only rendering and the combined
  native/client-rendering comparison remain separate tasks.

The result supports prioritizing a contained CEL-Go worker for the next
integration experiment. It does not establish production readiness or complete
the resource-limit and recovery tasks in `TASKLIST.md`.

Before cleanup, 245 tests passed across the Go, C++, Rust and existing game
runtime/state/value/client suites, including 44 Go-specific probes. After
cleanup, all 181 retained Go and game tests passed, Ruff passed, the dependency
lock validated, and a two-round Python/Go benchmark preserved output parity.
The retained Go and game suites can be run with:

```powershell
uv run pytest game/go_cel_test.py game/game_runtime_test.py game/game_state_test.py game/game_values_test.py game/game_client_test.py -n 0
uv run ruff check testlab/game_benchmark.py testlab/benchmark_go_cel.py game/go_cel.py game/go_cel_test.py
```

API references: [CEL-Go repository](https://github.com/cel-expr/cel-go),
[evaluation/cancellation options](https://pkg.go.dev/cel.dev/cel-go/cel),
[official Go downloads](https://go.dev/dl/).
