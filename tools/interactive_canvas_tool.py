"""A2UI-backed generative interfaces for the live canvas."""

from __future__ import annotations

import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from google.genai import types
from pydantic import BaseModel, Field

from providers import TextResponseProvider, TextResponseRequest
from tools.base_tool import BaseTools, logged_tool_call, single_flight, with_cooldown

logger = logging.getLogger(__name__)

A2UI_VERSION = "v1.0"
CANVAS_CATALOG_ID = "https://narratron.com/a2ui/catalogs/canvas/v1"
SUPPORTED_COMPONENTS = {
    "Card", "Column", "Row", "Grid", "Text", "Progress", "Button", "Divider", "Icon"
}
CONTAINER_COMPONENTS = {"Column", "Row", "Grid"}
MAX_COMPONENTS = 24
MAX_SURFACES = 8
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class A2UISurfaceDraft(BaseModel):
    """Small host envelope around a standard A2UI v1.0 component tree."""

    target_surface_id: str = ""
    left_pct: float = Field(default=50, ge=2, le=98)
    top_pct: float = Field(default=55, ge=2, le=98)
    width_pct: float = Field(default=28, ge=14, le=55)
    persistent: bool = False
    components: list[dict[str, Any]]
    data_model: dict[str, Any] = Field(default_factory=dict)


class InteractiveCanvasTools(BaseTools):
    """Delegate canvas-aware UI composition to a constrained A2UI generator."""

    def __init__(
        self,
        config: Optional[dict] = None,
        theater_id: str = "",
        canvas_state_service: Any = None,
        text_response_provider: Optional[TextResponseProvider] = None,
        generator: Optional[Callable[[str, Optional[bytes], str], Any]] = None,
    ) -> None:
        super().__init__(
            config=config,
            theater_id=theater_id,
            canvas_state_service=canvas_state_service,
            default_cooldown=10.0,
        )
        self.text_response_provider = text_response_provider
        self.generator = generator
        self.model = str((config or {}).get("model", "gemini-3.7-flash"))
        self.max_surfaces = max(1, min(MAX_SURFACES, int((config or {}).get("max_surfaces", 5))))

    def _canvas(self) -> Any:
        if self.canvas_state_service is None:
            raise RuntimeError("Canvas state is unavailable.")
        return self.canvas_state_service.get(self.active_theater_id)

    def _canvas_context(self) -> tuple[str, Optional[bytes], str]:
        canvas = self._canvas()
        latest = canvas.get_latest_state()
        current_surfaces = latest.get("interactive_surfaces", [])
        context = {
            "image_prompt": latest.get("prompt", ""),
            "narration": latest.get("narration", ""),
            "dialogue": latest.get("scene_dialogue", []),
            "scene_notes": latest.get("sticky_notes", []),
            "existing_surfaces": current_surfaces,
        }
        image_bytes = None
        mime_type = "image/png"
        try:
            _, image_path, _, _ = canvas._resolve_active_image()
            if image_path and Path(image_path).is_file():
                image_path = Path(image_path)
                image_bytes = image_path.read_bytes()
                mime_type = {
                    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"
                }.get(image_path.suffix.lower(), "image/png")
        except Exception as exc:
            logger.debug("Could not attach canvas image to A2UI generator: %s", exc)
        # Keep multimodal prompt size bounded in long-running theaters while
        # still showing the UI agent the exact current A2UI definitions.
        return json.dumps(context, ensure_ascii=False)[:30_000], image_bytes, mime_type

    def _prompt(self, request: str, canvas_context: str) -> str:
        return f"""You are Narratron's canvas UI designer. Design one compact, tasteful interface
that belongs over the current canvas. It may be narrative or non-narrative. The host positions it using percentages.
Return only an A2UISurfaceDraft.

User request: {request}
Current canvas context: {canvas_context}

First decide whether the request changes one of existing_surfaces or requires a new surface.
- To update, set target_surface_id to the exact surface_id copied from existing_surfaces and return
  that surface's complete replacement component tree and data model.
- To add new UI, set target_surface_id to an empty string. Avoid duplicating an existing tracker or
  interactable. Never invent or alter a surface ID.

Use the Narratron Canvas Catalog components only: Card, Column, Row, Grid, Text, Progress,
Button, Divider, Icon. It extends A2UI {A2UI_VERSION}'s Basic Catalog with Grid and Progress.
Produce a flat adjacency list with a component whose id is `root`.
Card uses `child`; Column/Row/Grid use `children`; Button uses `child`; Text uses `text`.
Grid uses integer `columns` from 1-6. Progress uses numeric or data-bound `value`, optional
numeric or data-bound `max` (default 100), and optional `label`; it is display-only and needs no action.
Every Button must use action.event with a short identifier `name` and a literal context object.
The context MUST contain `userAction`, phrased as the user's intended selection or request. For
example, "Grab the sword" sends "I grab the sword", while "Show details" sends "Show the details". Use at most three buttons and 24
components. Keep copy brief. Do not include HTML, JavaScript, URLs, markdown links, remote images,
or unsupported properties. Position the surface near the relevant visual object without obscuring
the scene's center or bottom dialogue area.

Set persistent=true only for a cross-scene HUD or tracker that should remain useful after the scene
image changes, such as health, inventory, currency, objectives, or status. Scene-specific choices,
object interactions, clues, and flavor cards must use persistent=false."""

    def _generate(self, prompt: str, image_bytes: Optional[bytes], mime_type: str) -> A2UISurfaceDraft:
        if self.generator:
            result = self.generator(prompt, image_bytes, mime_type)
            return result if isinstance(result, A2UISurfaceDraft) else A2UISurfaceDraft.model_validate(result)

        provider = self.text_response_provider
        client = getattr(provider, "client", None)
        if client is not None:
            contents: list[Any] = [prompt]
            if image_bytes:
                contents.append(types.Part.from_bytes(data=image_bytes, mime_type=mime_type))
            response = client.models.generate_content(
                model=self.model,
                contents=contents,
                config=types.GenerateContentConfig(
                    temperature=0.7,
                    max_output_tokens=4096,
                    response_mime_type="application/json",
                    response_schema=A2UISurfaceDraft,
                ),
            )
            parsed = getattr(response, "parsed", None)
            if parsed is not None:
                return parsed if isinstance(parsed, A2UISurfaceDraft) else A2UISurfaceDraft.model_validate(parsed)
            return A2UISurfaceDraft.model_validate_json(response.text)

        if provider is None:
            raise RuntimeError("The A2UI generator is not configured.")
        response = provider.generate(TextResponseRequest(
            prompt=prompt,
            temperature=0.7,
            max_output_tokens=4096,
            response_schema=A2UISurfaceDraft,
        ))
        parsed = response.parsed or json.loads(response.text)
        return parsed if isinstance(parsed, A2UISurfaceDraft) else A2UISurfaceDraft.model_validate(parsed)

    @staticmethod
    def _validate_components(components: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not components or len(components) > MAX_COMPONENTS:
            raise ValueError(f"A surface must contain 1-{MAX_COMPONENTS} components.")
        clean = json.loads(json.dumps(components))
        ids: set[str] = set()
        for component in clean:
            component_id = component.get("id")
            kind = component.get("component")
            if not isinstance(component_id, str) or not _IDENTIFIER.fullmatch(component_id):
                raise ValueError("Every component needs a safe A2UI identifier.")
            if component_id in ids:
                raise ValueError(f"Duplicate A2UI component id: {component_id}")
            if kind not in SUPPORTED_COMPONENTS:
                raise ValueError(f"Unsupported A2UI component: {kind}")
            ids.add(component_id)
            if kind == "Text" and not isinstance(component.get("text"), (str, dict)):
                raise ValueError(f"Text component {component_id} needs text.")
            if kind in CONTAINER_COMPONENTS and not isinstance(component.get("children"), list):
                raise ValueError(f"{kind} component {component_id} needs children.")
            if kind == "Grid":
                columns = component.get("columns")
                if not isinstance(columns, int) or isinstance(columns, bool) or not 1 <= columns <= 6:
                    raise ValueError(f"Grid component {component_id} needs integer columns from 1-6.")
            if kind == "Progress":
                if not isinstance(component.get("value"), (int, float, dict)) or isinstance(component.get("value"), bool):
                    raise ValueError(f"Progress component {component_id} needs a numeric or bound value.")
                maximum = component.get("max", 100)
                if not isinstance(maximum, (int, float, dict)) or isinstance(maximum, bool):
                    raise ValueError(f"Progress component {component_id} needs a numeric or bound max.")
            if kind in {"Card", "Button"} and not isinstance(component.get("child"), str):
                raise ValueError(f"{kind} component {component_id} needs a child.")
            if kind == "Button":
                event = (component.get("action") or {}).get("event")
                if not isinstance(event, dict) or not _IDENTIFIER.fullmatch(str(event.get("name", ""))):
                    raise ValueError(f"Button {component_id} needs a safe action.event name.")
                context = event.get("context")
                if not isinstance(context, dict):
                    raise ValueError(f"Button {component_id} action needs a literal context object.")
                action_text = context.get("userAction") or context.get("playerAction")
                if not str(action_text or "").strip():
                    raise ValueError(f"Button {component_id} action needs context.userAction.")
                # A literal-only event context keeps the browser renderer non-executable.
                if any(isinstance(value, (dict, list)) for value in context.values()):
                    raise ValueError(f"Button {component_id} action context must contain literals only.")
        if "root" not in ids:
            raise ValueError("The A2UI tree must contain a root component.")
        for component in clean:
            refs = list(component.get("children") or [])
            if component.get("child"):
                refs.append(component["child"])
            if any(ref not in ids for ref in refs):
                raise ValueError(f"Component {component['id']} references an unknown child.")
        return clean

    @single_flight(timeout=30.0, error_message="An interactive canvas design is already in progress.")
    @with_cooldown("updating the interactive canvas")
    def update_interactive_canvas(self, request: str) -> dict[str, Any]:
        """Ask the UI agent to add new UI or update the relevant current surface.

        Args:
            request: The UI content or state change to represent on the current canvas.
        """
        request = " ".join(str(request or "").split())[:1000]
        if not request:
            return {"error": "Describe the UI or update to apply."}
        canvas = self._canvas()
        context, image_bytes, mime_type = self._canvas_context()
        try:
            draft = self._generate(self._prompt(request, context), image_bytes, mime_type)
            components = self._validate_components(draft.components)
        except Exception as exc:
            logger.warning("A2UI canvas generation failed: %s", exc)
            return {"error": f"Could not update the interactive canvas: {exc}"}

        target_surface_id = str(draft.target_surface_id or "").strip()[:100]
        existing = canvas.interactive_surfaces.get(target_surface_id) if target_surface_id else None
        if target_surface_id and not existing:
            return {"error": "The UI agent selected a surface that is no longer active."}

        if not existing:
            surface_id = f"canvas_{uuid.uuid4().hex}"
            message = {
                "version": A2UI_VERSION,
                "createSurface": {
                    "surfaceId": surface_id,
                    "catalogId": CANVAS_CATALOG_ID,
                    "sendDataModel": True,
                    "components": components,
                    "dataModel": draft.data_model,
                },
            }
            surface = {
                "surface_id": surface_id,
                "persistent": bool(draft.persistent),
                "placement": {
                    "left_pct": round(draft.left_pct, 2),
                    "top_pct": round(draft.top_pct, 2),
                    "width_pct": round(draft.width_pct, 2),
                },
                "messages": [message],
            }
            canvas.upsert_interactive_surface(surface, max_surfaces=self.max_surfaces)
            return {
                "status": "displayed",
                "surface_id": surface_id,
                "persistent": bool(draft.persistent),
                "component_count": len(components),
            }

        updated = json.loads(json.dumps(existing))
        create_message = next(
            (message for message in updated.get("messages", []) if message.get("createSurface")),
            None,
        )
        if create_message is None:
            return {"error": "The existing surface has no A2UI createSurface message."}
        updated["messages"] = [
            create_message,
            {
                "version": A2UI_VERSION,
                "updateComponents": {"surfaceId": target_surface_id, "components": components},
            },
            {
                "version": A2UI_VERSION,
                "updateDataModel": {"surfaceId": target_surface_id, "path": "/", "value": draft.data_model},
            },
        ]
        # User drag placement wins over regenerated coordinates. Once a HUD is
        # persistent, an incidental content update must not demote it.
        updated["persistent"] = bool(existing.get("persistent", False) or draft.persistent)
        canvas.upsert_interactive_surface(updated, max_surfaces=self.max_surfaces)
        return {
            "status": "updated",
            "surface_id": target_surface_id,
            "persistent": updated["persistent"],
            "component_count": len(components),
        }

    @logged_tool_call
    def clear_interactive_canvas(self) -> dict[str, Any]:
        """Remove all generated A2UI surfaces from the current canvas."""
        removed = self._canvas().delete_interactive_surface("all")
        return {"status": "cleared", "removed": removed}
