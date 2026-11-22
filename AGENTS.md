# Project Rules

## Python Command Execution with `uv`

Always use `uv` when running Python commands, executing scripts, running tests, or managing dependencies in this project.

### Rules & Guidelines

- **Running Scripts**: Always execute Python scripts with `uv run`:
  ```bash
  uv run python <script_path>
  # or
  uv run <script_path>
  ```

- **Running Modules**: Use `uv run python -m`:
  ```bash
  uv run python -m uvicorn main:app --reload
  ```

- **Running Tests & Tools**: Always invoke test runners, linters, and other CLI tools through `uv run`:
  ```bash
  uv run pytest -n 4
  uv run ruff check .
  ```

- **Dependency Management**: Use `uv` instead of bare `pip`:
  ```bash
  uv add <package>
  uv remove <package>
  uv pip install <package>
  ```

- **Do Not Run Bare Python**: Avoid bare `python`, `python3`, `pip`, or `pytest` commands without `uv run`.

## Installing `uv`

If `uv` is not installed on the system, follow the official installation guide from Astral:
- [Astral `uv` Installation Documentation](https://docs.astral.sh/uv/getting-started/installation/)


## Constructor Dependencies Must Be Explicit

- **Required Dependencies Are Required Arguments**: If a class needs a service, manager, repository, provider, or other collaborator to operate, require a typed constructor argument without a default. Pass the dependency explicitly from the application composition root or factory.
- **Optional Means Absence Is Supported**: Use an optional constructor argument only when the class has meaningful, documented behavior without that dependency. Do not use `None` to mean "look up the real dependency later."
- **No Hidden Dependency Resolution**: Do not replace omitted constructor dependencies with services from `object_registry`, another global singleton, or an implicit service factory. This also applies to properties and methods that lazily resolve constructor dependencies. Resolve application services at the composition root and inject them.
- **Tests Supply Their Dependencies**: Unit tests must pass properly configured mocks, stubs, or fakes for required collaborators. Do not make production dependencies optional to simplify test construction or preserve incomplete test fixtures.

## Python Development: No `hasattr` or `getattr` (Unless True Reflection)

Never use `hasattr` or `getattr` in Python code unless performing operations that explicitly require dynamic reflection or introspection on Python objects or methods.

### Rules & Guidelines

- **No Defensive Attribute Probing**: Never use `hasattr` or `getattr` to probe whether an object, service, manager, or dependency has a method or property (e.g. avoid `if hasattr(self.canvas_manager, "story"):`).
- **No Dodging Proper Test Mocks**: Never use `hasattr` or `getattr` in production code or tests to dodge configuring proper test mocks or to accommodate incomplete test doubles. If a test mock raises `AttributeError` or lacks an attribute, properly configure the test double/mock to satisfy the interface. Never weaken or pollute production code with defensive attribute checks just to make incomplete test mocks pass.
- **No Dict or Model Probing**: Never use `hasattr` or `getattr` for dictionary-like lookups or checking optional fields on Pydantic models, dataclasses, or structured data objects.
- **Allowed Exception (Reflection Only)**: `hasattr` and `getattr` are strictly reserved for genuine dynamic reflection and metaprogramming where attribute or method names are dynamic, determined at runtime, and cannot be known at write time (such as dynamic plugin discovery, dispatch by arbitrary runtime string, or custom serialization engines).
- **Required Alternatives**:
  - **Explicit Access & Optional Checks**: Use direct attribute access (`obj.attr`). If an attribute or dependency may be absent or uninitialized, explicitly define it on the class or schema with default `None` (or `Optional[...]`) and check `if obj.attr is not None:`.
  - **Explicit Protocols & Types**: Use `abc.ABC` or `typing.Protocol` to define and verify contracts instead of duck-typing with `hasattr`.
  - **Dictionary Lookups**: For mappings and dictionaries, use key checks or `.get()` (`key in d`, `d.get("key")`).
  - **Proper Mock Setup in Tests**: When writing unit tests, properly configure test fixtures, stubs, and mocks (`unittest.mock.MagicMock`, `create_autospec`, or explicit dummy classes) with all attributes and methods expected by production code.


## Python Development: No `isinstance`, No `Any`, No `object`, and Mandatory Type Annotations

### Rules & Guidelines

- **No `isinstance`**: Never use `isinstance` in Python code for runtime type dispatch, branching, or conditional checks.
  - Rely on polymorphism, class inheritance, dedicated handler methods, or distinct specialized functions instead of branching on types at runtime.
  - Do not use `isinstance` in tests to assert types; assert specific properties, state, or behaviors instead.
- **No `Any`**: Never use `typing.Any` in type hints, signatures, or annotations.
  - Use specific, concrete types, `TypedDict`, or Pydantic models.
  - Use `typing.Protocol` or `abc.ABC` to specify interface contracts.
  - Use `typing.TypeVar` or generics when functions/classes operate over generalized types.
- **Mandatory Complete Type Annotations**: Every function, method, and coroutine must be fully type-annotated:
  - All parameters must have explicit type annotations.
  - Return types must always be annotated explicitly (use `-> None` for functions that return nothing).



## Testing

### Development checks

Install the Chromium browsers required by the Node and Python Playwright
packages, then run the checks from the repository root:

```powershell
npx playwright install chromium
uv run python -m playwright install chromium
npm test
uv run pytest -n 4
uv run ruff check .
```

`npm test` checks TypeScript types for production code and tests, builds the
browser modules, and runs the TypeScript Playwright suite in Chromium. Frontend
tests live beside their sources in `frontend/**/*_test.ts`; shared fixtures
and their tests live in `testing/ui/`. Python tests cover backend behavior and
the remaining browser/server integration checks. Both suites isolate unexpected
network access; live provider evaluations are separate.

**Avoid using all available CPU cores when running tests.** Leave CPU headroom
for the host machine and any concurrent agents or tools:

- Never run `pytest` with uncapped `-n auto` or `-n logical`, and do not pass a
  Playwright `--workers` count equal to the machine's total CPU count.
- Keep parallel workers at or below half of the available CPU cores (default
  cap: `4` workers). `pytest.ini` caps default `pytest-xdist` workers via
  `--maxprocesses=4`, and `playwright.config.ts` caps default workers at
  `min(4, floor(cpus / 2))`. On machines with fewer than 8 cores or when
  multiple agents run concurrently, lower worker counts explicitly (for example
  `-n 2` / `--workers=2`, or serial).
- For single-file or small targeted runs, prefer serial execution
  (`uv run pytest -n 0 <path>` and `--workers=1`) to avoid worker startup
  overhead and unnecessary CPU spikes.

For a focused browser test, build first and pass Playwright options through the
UI script:

```powershell
npm run build
npm run test:ui -- frontend/canvas/cursor-state_test.ts --workers=1
```

`npm run test:ui` uses existing generated assets, so rebuild after changing
TypeScript or keep `npm run watch` running. Generated files in
`static/generated/` are ignored by Git; edit `frontend/**/*.ts` and use
`.js` extensions for relative browser imports. Templates load modules from
`/static/generated/<consumer>/`. See the [frontend layout](frontend/README.md).
Docker compiles these assets in its Node build stage.

See the [testing guide](skills/narratron-testing/SKILL.md) for fixtures, offline
guards, and evaluation commands, and the
[migration inventory](frontend/canvas/MIGRATION.md) for coverage parity and
remaining optional improvements.

### Local testing

For local testing, run `uv run main.py --testing_use_local`. This uses local
resources, preserves the existing `deployer.db` and `theaters/` directory, and
automatically signs unauthenticated browsers in as the seeded `localtest` user
(password `narratron`) on the initial page visit for each unique server run. A
unique server run UUID is tracked in frontend browser storage; subsequent visits
and other browser tabs retain their logged-out or chosen account session so you
can test multi-party behavior with different accounts.

To discard local database state and start with a new database, add
`--with_empty_db`. This does not delete theater files.


To load test a disposable local canvas, run:

```powershell
uv run testing/e2e/load_canvas_viewers.py --viewers 25 --trials 5 --testcase desert_basic
```

The harness starts and stops its own local server, creates a disposable canvas,
then creates a separate local account and isolated browser context for every
viewer. It streams the selected testcase narration to the connected agent,
measures when the generated image is fully visible to each viewer, and writes
per-viewer latencies plus trial mean and standard deviation to
`evaluation_results/`.

To load test a baton handoff to a secondary authenticated viewer while other
viewers remain connected, run:

```powershell
uv run testing/e2e/load_canvas_baton_handoff.py --viewers 25 --trials 5
```

The first viewer receives and accepts the baton; the remaining viewers stay
connected as spectators. The harness verifies the baton UI updates for every
viewer and writes request and handoff delivery timings to `evaluation_results/`.

## Skills & Workflows

Project-specific agent skills and procedural runbooks are located in the [`skills/`](skills/) directory:

- [`skills/narratron-testing/SKILL.md`](skills/narratron-testing/SKILL.md): Testing guidelines, naming conventions, mock patterns, and automated end-to-end narration evaluation.
- [`skills/writing-adventures/SKILL.md`](skills/writing-adventures/SKILL.md): Authoring guide, lore structuring patterns, theater.yaml configuration, testing runbooks, and validation checklist for interactive adventures.
- [`skills/developing-games/SKILL.md`](skills/developing-games/SKILL.md): Read when creating, editing, or playtesting a Narratron game package, including games embedded in adventures.
- [`skills/developing-avatars/SKILL.md`](skills/developing-avatars/SKILL.md): Read when creating, editing, or previewing a Narratron 3D avatar package.

