"""Tests for local Canvas TypeScript build verification helper."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from utils.frontend_build import ensure_canvas_assets_built


def test_skips_build_when_frontend_sources_are_absent(tmp_path: Path) -> None:
    with patch("utils.frontend_build.subprocess.run") as run_mock:
        ensure_canvas_assets_built(project_root=tmp_path)
    run_mock.assert_not_called()


@pytest.mark.parametrize("consumer", ["canvas", "pages", "shared", "remote", "audio", "game"])
def test_skips_build_when_generated_outputs_are_up_to_date(tmp_path: Path, consumer: str) -> None:
    source = tmp_path / "frontend" / consumer / "module.ts"
    source.parent.mkdir(parents=True)
    source.write_text("export const x = 1;\n", encoding="utf-8")

    output = tmp_path / "frontend" / "static" / "generated" / consumer / "module.js"
    output.parent.mkdir(parents=True)
    output.write_text("export const x = 1;\n", encoding="utf-8")

    os.utime(source, (1000, 1000))
    os.utime(output, (2000, 2000))

    with patch("utils.frontend_build.subprocess.run") as run_mock:
        ensure_canvas_assets_built(project_root=tmp_path)
    run_mock.assert_not_called()


@pytest.mark.parametrize("consumer", ["canvas", "pages", "shared", "remote", "audio", "game"])
def test_runs_npm_build_when_generated_output_is_missing_or_stale(tmp_path: Path, consumer: str) -> None:
    source = tmp_path / "frontend" / consumer / "module.ts"
    source.parent.mkdir(parents=True)
    source.write_text("export const x = 1;\n", encoding="utf-8")

    with (
        patch("utils.frontend_build.shutil.which", return_value="npm"),
        patch("utils.frontend_build.subprocess.run") as run_mock,
    ):
        ensure_canvas_assets_built(project_root=tmp_path)

    run_mock.assert_called_once_with(["npm", "run", "build"], cwd=tmp_path, check=True)

    output = tmp_path / "frontend" / "static" / "generated" / consumer / "module.js"
    output.parent.mkdir(parents=True)
    output.write_text("export const x = 1;\n", encoding="utf-8")
    os.utime(output, (1000, 1000))
    os.utime(source, (2000, 2000))

    with (
        patch("utils.frontend_build.shutil.which", return_value="npm"),
        patch("utils.frontend_build.subprocess.run") as run_mock,
    ):
        ensure_canvas_assets_built(project_root=tmp_path)

    run_mock.assert_called_once_with(["npm", "run", "build"], cwd=tmp_path, check=True)


def test_raises_when_build_needed_and_npm_missing(tmp_path: Path) -> None:
    source = tmp_path / "frontend" / "canvas" / "cursor-state.ts"
    source.parent.mkdir(parents=True)
    source.write_text("export const x = 1;\n", encoding="utf-8")

    with patch("utils.frontend_build.shutil.which", return_value=None):
        with pytest.raises(RuntimeError, match="npm"):
            ensure_canvas_assets_built(project_root=tmp_path)


def test_ignores_tests_excluded_from_production_build(tmp_path: Path) -> None:
    source = tmp_path / "frontend" / "canvas" / "cursor-state.ts"
    source.parent.mkdir(parents=True)
    source.write_text("export const x = 1;\n", encoding="utf-8")
    output = tmp_path / "frontend" / "static" / "generated" / "canvas" / "cursor-state.js"
    output.parent.mkdir(parents=True)
    output.write_text("export const x = 1;\n", encoding="utf-8")
    os.utime(source, (1000, 1000))
    os.utime(output, (2000, 2000))
    for name in ("cursor-state_test.ts", "legacy_test.js", "legacy.js", "legacy.jsx", "legacy.tsx"):
        (source.parent / name).write_text("// Not emitted by tsconfig.build.json\n", encoding="utf-8")

    with patch("utils.frontend_build.subprocess.run") as run_mock:
        ensure_canvas_assets_built(project_root=tmp_path)
    run_mock.assert_not_called()


def test_rebuilds_when_declaration_changes(tmp_path: Path) -> None:
    source = tmp_path / "frontend" / "canvas" / "cursor-state.ts"
    source.parent.mkdir(parents=True)
    source.write_text("export const x = 1;\n", encoding="utf-8")
    output = tmp_path / "frontend" / "static" / "generated" / "canvas" / "cursor-state.js"
    output.parent.mkdir(parents=True)
    output.write_text("export const x = 1;\n", encoding="utf-8")
    os.utime(source, (1000, 1000))
    os.utime(output, (2000, 2000))
    (source.parent / "globals.d.ts").write_text("declare const x: number;\n", encoding="utf-8")

    with (
        patch("utils.frontend_build.shutil.which", return_value="npm"),
        patch("utils.frontend_build.subprocess.run") as run_mock,
    ):
        ensure_canvas_assets_built(project_root=tmp_path)
    run_mock.assert_called_once_with(["npm", "run", "build"], cwd=tmp_path, check=True)


def test_ignores_static_and_templates_directories_under_frontend(tmp_path: Path) -> None:
    static_file = tmp_path / "frontend" / "static" / "generated" / "canvas" / "sample.ts"
    static_file.parent.mkdir(parents=True)
    static_file.write_text("export const ignored = true;\n", encoding="utf-8")

    template_file = tmp_path / "frontend" / "templates" / "canvas.html"
    template_file.parent.mkdir(parents=True)
    template_file.write_text("<html></html>\n", encoding="utf-8")

    with patch("utils.frontend_build.subprocess.run") as run_mock:
        ensure_canvas_assets_built(project_root=tmp_path)
    run_mock.assert_not_called()


@pytest.mark.parametrize("section", ["labs", "benchmarks"])
def test_testlab_frontend_build_only_checked_when_requested(tmp_path: Path, section: str) -> None:
    source = tmp_path / "testlab" / "frontend" / section / "sample.ts"
    source.parent.mkdir(parents=True)
    source.write_text("export const x = 1;\n", encoding="utf-8")

    with patch("utils.frontend_build.subprocess.run") as run_mock:
        ensure_canvas_assets_built(project_root=tmp_path)
    run_mock.assert_not_called()

    with (
        patch("utils.frontend_build.shutil.which", return_value="npm"),
        patch("utils.frontend_build.subprocess.run") as run_mock,
    ):
        ensure_canvas_assets_built(project_root=tmp_path, include_testlab=True)
    run_mock.assert_called_once_with(["npm", "run", "build"], cwd=tmp_path, check=True)

    output = tmp_path / "testlab" / "frontend" / "static" / "generated" / section / "sample.js"
    output.parent.mkdir(parents=True)
    output.write_text("export const x = 1;\n", encoding="utf-8")
    os.utime(source, (1000, 1000))
    os.utime(output, (2000, 2000))

    with patch("utils.frontend_build.subprocess.run") as run_mock:
        ensure_canvas_assets_built(project_root=tmp_path, include_testlab=True)
    run_mock.assert_not_called()
