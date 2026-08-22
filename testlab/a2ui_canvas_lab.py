"""Reusable A2UI Canvas smoke-test helpers for Test Lab and CLI diagnostics."""

from __future__ import annotations

import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from components.canvas_state_service import CanvasStateService
from components.theater_manager import TheaterManager
from providers import TextResponseProvider
from tools.interactive_canvas_tool import InteractiveCanvasTools

HEALTH_BARS_REQUEST = (
    "Create exactly two independent surfaces, one beneath each knight. Each surface must contain a "
    "separate Card with one full horizontal health Progress bar: Knight 1 and Knight 2 respectively. "
    "Both bars must have value 100, max 100, and variant health. Do not put both bars in one surface."
)
DEFAULT_CANVAS_REQUEST = "Create a compact, useful interface for the current scene."


@dataclass(frozen=True)
class A2UICanvasTestConfig:
    """Declarative fixture configuration for one isolated A2UI generation turn."""

    name: str = "a2ui-canvas-test"
    request: str = DEFAULT_CANVAS_REQUEST
    model: str = "gemini-3.7-flash"
    image_path: Path | None = None
    expected_surface_count: int = 1
    expected_persistent: bool | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["image_path"] = str(self.image_path) if self.image_path else None
        return payload


def default_canvas_config(**overrides: Any) -> A2UICanvasTestConfig:
    """Return the general-purpose browser/TestLab fixture."""
    return A2UICanvasTestConfig(**overrides)


def health_bars_smoke_config(**overrides: Any) -> A2UICanvasTestConfig:
    """Return the explicit two-surface health-bar fixture used only by the CLI smoke test."""
    return A2UICanvasTestConfig(
        name="two-knight-health-bars-smoke",
        request=HEALTH_BARS_REQUEST,
        expected_surface_count=2,
        **overrides,
    )


def build_canvas_tool(
    provider: TextResponseProvider,
    *,
    model: str = "gemini-3.7-flash",
    theater_root: Path,
    image_path: Path | None = None,
) -> tuple[InteractiveCanvasTools, CanvasStateService, str]:
    """Build an isolated canvas tool without using a live theater's state."""
    theater_id = "a2ui_canvas_smoke"
    theater_manager = TheaterManager(base_theaters_dir=theater_root)
    canvas_state_service = CanvasStateService(theater_manager)
    if image_path is not None:
        canvas_state_service.show_image(str(image_path), theater_id=theater_id)
    return (
        InteractiveCanvasTools(
            {"max_surfaces": 8},
            theater_id=theater_id,
            canvas_state_service=canvas_state_service,
            text_response_provider=provider,
            model=model,
        ),
        canvas_state_service,
        theater_id,
    )


def verify_surfaces(surfaces: list[dict[str, Any]], config: A2UICanvasTestConfig) -> list[str]:
    """Return human-readable invariant failures across a configured surface batch."""
    errors: list[str] = []
    if len(surfaces) != config.expected_surface_count:
        errors.append(f"Expected {config.expected_surface_count} surfaces, received {len(surfaces)}.")
    for surface in surfaces:
        create = next((message.get("createSurface") for message in surface.get("messages", []) if message.get("createSurface")), None)
        if not isinstance(create, dict) or not isinstance(create.get("components"), list):
            errors.append("Surface has no createSurface component list.")
            continue
        current_components = create["components"]
        if "root" not in {component.get("id") for component in current_components if isinstance(component, dict)}:
            errors.append("Component tree has no root component.")
    if config.expected_persistent is not None:
        for surface in surfaces:
            if bool(surface.get("persistent")) != config.expected_persistent:
                errors.append(f"Expected persistent={config.expected_persistent}, received {bool(surface.get('persistent'))}.")
    return errors


def run_canvas_test(
    provider: TextResponseProvider,
    *,
    config: A2UICanvasTestConfig,
) -> dict[str, Any]:
    """Generate and validate a configured A2UI fixture against an isolated canvas state."""
    with tempfile.TemporaryDirectory(prefix="narratron-a2ui-lab-") as temp_dir:
        tools, service, theater_id = build_canvas_tool(
            provider,
            model=config.model,
            theater_root=Path(temp_dir),
            image_path=config.image_path,
        )
        started = time.monotonic()
        result = tools.update_interactive_canvas(config.request)
        elapsed_ms = round((time.monotonic() - started) * 1000)
        output: dict[str, Any] = {
            "config": config.as_dict(),
            "result": result,
            "elapsed_ms": elapsed_ms,
            "surfaces": service.get(theater_id).get_latest_state().get("interactive_surfaces", []),
        }
        if not isinstance(result, dict):
            output["errors"] = [str(result)]
            return output
        if result.get("status") not in {"displayed", "updated", "applied"}:
            output["errors"] = [str(result.get("error") or "No surface was created.")]
            return output
        output["errors"] = verify_surfaces(output["surfaces"], config)
        return output


def run_health_bars_smoke(
    provider: TextResponseProvider,
    *,
    model: str = "gemini-3.7-flash",
    image_path: Path | None = None,
) -> dict[str, Any]:
    """Backward-compatible shortcut for the default two-knight fixture."""
    return run_canvas_test(
        provider,
        config=health_bars_smoke_config(model=model, image_path=image_path),
    )
