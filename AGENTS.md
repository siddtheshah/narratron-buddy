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
  uv run pytest
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


## Python Development: No `isinstance`, No `Any`, and Mandatory Type Annotations

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



## Skills & Workflows

Project-specific agent skills and procedural runbooks are located in the [`skills/`](skills/) directory:

- [`skills/narratron-testing/SKILL.md`](skills/narratron-testing/SKILL.md): Testing guidelines, naming conventions, mock patterns, and automated end-to-end narration evaluation.
- [`skills/writing-adventures/SKILL.md`](skills/writing-adventures/SKILL.md): Authoring guide, lore structuring patterns, theater.yaml configuration, testing runbooks, and validation checklist for interactive adventures.

