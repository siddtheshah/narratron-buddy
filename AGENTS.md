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


## Skills & Workflows

Project-specific agent skills and procedural runbooks are located in the [`skills/`](skills/) directory:

- [`skills/narratron-testing/SKILL.md`](skills/narratron-testing/SKILL.md): Testing guidelines, naming conventions, mock patterns, and automated end-to-end narration evaluation.
- [`skills/writing-adventures/SKILL.md`](skills/writing-adventures/SKILL.md): Authoring guide, lore structuring patterns, theater.yaml configuration, testing runbooks, and validation checklist for interactive adventures.
