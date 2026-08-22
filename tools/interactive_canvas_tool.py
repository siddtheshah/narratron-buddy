"""A2UI-backed generative interfaces for the live canvas."""

from __future__ import annotations

import json
import logging
import re
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Optional

from jsonschema import Draft202012Validator
from pydantic import BaseModel, Field

from providers import TextResponseAttachment, TextResponseProvider, TextResponseRequest
from tools.base_tool import BaseTools, logged_tool_call, single_flight, with_cooldown

logger = logging.getLogger(__name__)
LOG_PREFIX = "[InteractiveCanvasTools]"

A2UI_VERSION = "v1.0"
MAX_COMPONENTS = 24
MAX_SURFACES = 8
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_IDENTIFIER_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"
_COMMON_REF_PREFIX = "https://a2ui.org/specification/v1_0/common_types.json#/$defs/"
CANVAS_CATALOG_PATH = Path(__file__).with_name("a2ui_canvas_catalog.json")


def _load_canvas_catalog() -> dict[str, Any]:
    catalog = json.loads(CANVAS_CATALOG_PATH.read_text(encoding="utf-8"))
    if catalog.get("protocolVersion") != "1.0":
        raise RuntimeError("Narratron Canvas Catalog must target A2UI protocol version 1.0.")
    if not str(catalog.get("catalogId", "")).strip():
        raise RuntimeError("Narratron Canvas Catalog is missing catalogId.")
    components = catalog.get("components")
    if not isinstance(components, dict) or not components:
        raise RuntimeError("Narratron Canvas Catalog must define components.")
    return catalog


def _binding_schema(literal: dict[str, Any]) -> dict[str, Any]:
    return {
        "anyOf": [
            literal,
            {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "pattern": r"^/",
                        "description": "JSON Pointer into the surface data model.",
                    }
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        ]
    }


def _common_type_for_generation(name: str) -> dict[str, Any]:
    identifier = {"type": "string", "pattern": _IDENTIFIER_PATTERN}
    if name == "ComponentId":
        return identifier
    if name == "ChildList":
        # The canvas renderer intentionally supports only the static ChildList
        # form, not template-driven children.
        return {"type": "array", "items": identifier}
    if name == "DynamicString":
        return _binding_schema({"type": "string"})
    if name == "DynamicNumber":
        return _binding_schema({"type": "number"})
    if name == "Action":
        literal = {"anyOf": [{"type": "string"}, {"type": "number"}, {"type": "boolean"}]}
        return {
            "type": "object",
            "properties": {
                "event": {
                    "type": "object",
                    "properties": {
                        "name": identifier,
                        "context": {
                            "type": "object",
                            "properties": {"userAction": {"type": "string", "minLength": 1}},
                            "required": ["userAction"],
                            "additionalProperties": literal,
                        },
                    },
                    "required": ["name", "context"],
                    "additionalProperties": False,
                }
            },
            "required": ["event"],
            "additionalProperties": False,
        }
    raise RuntimeError(f"Canvas catalog uses unsupported A2UI common type: {name}")


def _resolve_generation_schema(value: Any) -> Any:
    """Resolve the catalog subset into Gemini's supported JSON Schema subset."""
    if isinstance(value, list):
        return [_resolve_generation_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    ref = value.get("$ref")
    if isinstance(ref, str) and ref.startswith(_COMMON_REF_PREFIX):
        resolved = _common_type_for_generation(ref.removeprefix(_COMMON_REF_PREFIX))
        description = value.get("description")
        if description:
            resolved["description"] = description
        return resolved
    resolved = {
        key: _resolve_generation_schema(item)
        for key, item in value.items()
        if key not in {"$ref", "unevaluatedProperties", "discriminator"}
    }
    if "const" in resolved:
        resolved["enum"] = [resolved.pop("const")]
        # Vertex requires an explicit schema type even when an enum has only
        # one permitted value (the A2UI component discriminator).
        resolved.setdefault("type", "string")
    return resolved


def _component_validation_schema(catalog: dict[str, Any]) -> dict[str, Any]:
    """Resolve the catalog into a discriminated schema for local validation."""
    branches: list[dict[str, Any]] = []
    for name, definition in catalog["components"].items():
        properties: dict[str, Any] = {
            "id": {"type": "string", "pattern": _IDENTIFIER_PATTERN},
        }
        required = ["id"]
        for part in definition.get("allOf", []):
            ref = part.get("$ref") if isinstance(part, dict) else None
            if ref == f"{_COMMON_REF_PREFIX}ComponentCommon":
                continue
            resolved = _resolve_generation_schema(part)
            properties.update(resolved.get("properties", {}))
            required.extend(resolved.get("required", []))
        if "component" not in properties:
            raise RuntimeError(f"Canvas catalog component {name} has no component discriminator.")
        branches.append({
            "type": "object",
            "title": name,
            "description": definition.get("description", ""),
            "properties": properties,
            "required": list(dict.fromkeys(required)),
            "additionalProperties": False,
        })
    return {"anyOf": branches}


def _vertex_safe_schema(value: Any) -> Any:
    """Flatten the catalog schema to the conservative Vertex responseSchema subset."""
    if isinstance(value, list):
        return [_vertex_safe_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    if isinstance(value.get("anyOf"), list) and value["anyOf"]:
        # The canvas generator emits literal values. Data bindings remain valid
        # A2UI input but are intentionally omitted from this constrained output
        # projection because Vertex rejects nested object unions in responseSchema.
        return _vertex_safe_schema(value["anyOf"][0])
    return {
        key: _vertex_safe_schema(item)
        for key, item in value.items()
        if key not in {"additionalProperties", "pattern", "minimum", "maximum", "minLength", "maxLength"}
    }


def _component_generation_schema(catalog: dict[str, Any]) -> dict[str, Any]:
    """Build a single typed component object for Vertex constrained decoding."""
    validation_schema = _component_validation_schema(catalog)
    properties: dict[str, Any] = {"id": {"type": "string"}}
    for branch in validation_schema["anyOf"]:
        for property_name, property_schema in branch["properties"].items():
            if property_name != "component":
                safe_schema = _vertex_safe_schema(deepcopy(property_schema))
                existing = properties.get(property_name)
                if existing is None:
                    properties[property_name] = safe_schema
                elif isinstance(existing.get("enum"), list) and isinstance(safe_schema.get("enum"), list):
                    # A flattened projection must accept values for each
                    # component type; strict catalog validation follows.
                    properties[property_name] = {
                        **existing,
                        "enum": list(dict.fromkeys([*existing["enum"], *safe_schema["enum"]])),
                    }
    properties["component"] = {"type": "string", "enum": sorted(catalog["components"])}
    required = [
        name
        for name in properties
        # Vertex tends to emit only fields marked required. These simple
        # fields are conditionally required in the real catalog; irrelevant
        # placeholders are removed before strict validation below. `action`
        # is intentionally required here: otherwise Vertex emits Button
        # objects without their catalog-required action.event payload.
        if name not in {"weight", "justify", "align", "gap"}
    ]
    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


def _surface_generation_schema(catalog: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "title": "A2UISurfaceDraft",
        "properties": {
            "target_surface_id": {"type": "string", "maxLength": 100},
            "left_pct": {
                "type": "number", "minimum": 2, "maximum": 98,
                "description": "Horizontal CENTER of the surface as a percentage of canvas width; not its left edge.",
            },
            "top_pct": {
                "type": "number", "minimum": 2, "maximum": 98,
                "description": "Vertical CENTER of the surface as a percentage of canvas height; not its top edge.",
            },
            "width_pct": {
                "type": "number", "minimum": 14, "maximum": 55,
                "description": "Surface width as a percentage of canvas width.",
            },
            "persistent": {"type": "boolean"},
            "components": {
                "type": "array",
                "items": _component_generation_schema(catalog),
            },
        },
        "required": ["components"],
    }


def _draft_generation_schema(catalog: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "title": "A2UISurfaceBatchDraft",
        "properties": {
            "surfaces": {
                "type": "array",
                "items": _surface_generation_schema(catalog),
            },
        },
        "required": ["surfaces"],
    }


CANVAS_CATALOG = _load_canvas_catalog()
CANVAS_CATALOG_ID = str(CANVAS_CATALOG["catalogId"])
SUPPORTED_COMPONENTS = frozenset(CANVAS_CATALOG["components"])
CONTAINER_COMPONENTS = frozenset({"Column", "Row", "Grid"})
CANVAS_COMPONENT_SCHEMA = _component_validation_schema(CANVAS_CATALOG)
CANVAS_DRAFT_SCHEMA = _draft_generation_schema(CANVAS_CATALOG)
CANVAS_COMPONENT_VALIDATOR = Draft202012Validator(CANVAS_COMPONENT_SCHEMA)
COMPONENT_ALLOWED_FIELDS = {
    name: frozenset({"id", *definition.get("allOf", [{}, {}])[-1].get("properties", {})})
    for name, definition in CANVAS_CATALOG["components"].items()
}


class A2UISurfaceDraft(BaseModel):
    """Small host envelope around a standard A2UI v1.0 component tree."""

    target_surface_id: str = ""
    left_pct: float = Field(default=50, ge=2, le=98)
    top_pct: float = Field(default=55, ge=2, le=98)
    width_pct: float = Field(default=28, ge=14, le=55)
    persistent: bool = False
    components: list[dict[str, Any]]
    data_model: dict[str, Any] = Field(default_factory=dict)


class A2UISurfaceBatchDraft(BaseModel):
    """One model response may create or update several independently placed surfaces."""

    surfaces: list[A2UISurfaceDraft] = Field(min_length=1, max_length=MAX_SURFACES)


class InteractiveCanvasTools(BaseTools):
    """Delegate canvas-aware UI composition to a constrained A2UI generator."""

    def __init__(
        self,
        config: Optional[dict] = None,
        theater_id: str = "",
        canvas_state_service: Any = None,
        text_response_provider: Optional[TextResponseProvider] = None,
        model: Optional[str] = None,
    ) -> None:
        super().__init__(
            config=config,
            theater_id=theater_id,
            canvas_state_service=canvas_state_service,
            default_cooldown=10.0,
        )
        self.text_response_provider = text_response_provider
        # Model selection is application-owned. Theater YAML controls behavior
        # and enablement, and any theater-level model value is ignored.
        self.model = str(model or "gemini-3.7-flash")
        self.catalog = CANVAS_CATALOG
        self.response_schema = CANVAS_DRAFT_SCHEMA
        self.max_surfaces = max(1, min(MAX_SURFACES, int((config or {}).get("max_surfaces", 5))))
        logger.debug(
            "%s Initialized theater=%s model=%s catalog=%s components=%s cooldown=%.2fs max_surfaces=%d provider=%s",
            LOG_PREFIX,
            theater_id or "default",
            self.model,
            CANVAS_CATALOG_ID,
            sorted(SUPPORTED_COMPONENTS),
            self.cooldown_duration,
            self.max_surfaces,
            type(text_response_provider).__name__ if text_response_provider else "unconfigured",
        )

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
                logger.debug(
                    "%s Attached canvas image theater=%s path=%s mime=%s bytes=%d",
                    LOG_PREFIX,
                    self.active_theater_id or "default",
                    image_path,
                    mime_type,
                    len(image_bytes),
                )
        except Exception as exc:
            logger.debug("%s Could not attach canvas image: %s", LOG_PREFIX, exc)
        logger.debug(
            "%s Captured context theater=%s surfaces=%s prompt_chars=%d narration_chars=%d image=%s",
            LOG_PREFIX,
            self.active_theater_id or "default",
            [
                {
                    "id": item.get("surface_id"),
                    "persistent": bool(item.get("persistent", False)),
                    "messages": len(item.get("messages", [])),
                }
                for item in current_surfaces
                if isinstance(item, dict)
            ],
            len(str(latest.get("prompt", ""))),
            len(str(latest.get("narration", ""))),
            bool(image_bytes),
        )
        # Keep multimodal prompt size bounded in long-running theaters while
        # still showing the UI agent the exact current A2UI definitions.
        return json.dumps(context, ensure_ascii=False)[:30_000], image_bytes, mime_type

    def _prompt(self, request: str, canvas_context: str, *, has_canvas_image: bool) -> str:
        catalog_summary = "\n".join(
            f"- {name}: {definition.get('description', '')} Required fields: "
            + ", ".join(
                field
                for field in definition.get("allOf", [{}, {}])[-1].get("required", [])
                if field != "component"
            )
            for name, definition in self.catalog["components"].items()
        )
        return f"""You are Narratron's canvas UI designer. Design one compact, tasteful interface
that belongs over the current canvas. It may be narrative or non-narrative. The host positions it using percentages.
Return only an A2UISurfaceBatchDraft with a non-empty `surfaces` array. Each array item is one
independently placed A2UI surface. Use multiple surfaces when the request calls for independently
positioned UI, such as one health bar/card for each character.

User request: {request}
Current canvas context: {canvas_context}
Canvas image: {"An image is attached to this request. Inspect it as the current scene and use its subjects and open space when choosing placement." if has_canvas_image else "No canvas image is attached; rely on the text context."}

Placement contract (critical): `left_pct` and `top_pct` are the CENTER of the surface, not its
top-left corner. Keep the whole surface on screen: do not put a center close to an edge. For a surface
of width W, keep left_pct between approximately W/2 + 2 and 98 - W/2. Use a similarly conservative
vertical center (normally 10-90) so the surface is not clipped at the top or bottom.

For each requested surface, decide whether it updates one of existing_surfaces or creates a new surface.
- To update, set target_surface_id to the exact surface_id copied from existing_surfaces and return
  that surface's complete replacement component tree and data model.
- To add new UI, set target_surface_id to an empty string. Avoid duplicating an existing tracker or
  interactable. Never invent or alter a surface ID.

Use the loaded Narratron Canvas Catalog ({CANVAS_CATALOG_ID}). Its JSON Schema is enforced on your response.
Catalog instructions: {self.catalog.get('instructions', '')}
Catalog components:
{catalog_summary}

Use the smallest component tree that fulfills the request. Cards are optional visual grouping tools, not a
requirement. You may use separate Cards for distinct indicators (for example, one card per character health
bar) when that makes ownership or placement clearer. Do not add a shared Card solely to make unrelated
elements look tidy; follow any explicit request for cards, panels, or unframed controls.

Structural rules are mandatory: Card has exactly one `child` ID; Column and Row need a `children`
array; Grid needs `children` and `columns`; Button needs both `child` and `action`; Text needs `text`.
Every referenced child ID must be the id of another component in this same flat list. A Progress should
include value, max, label, and variant whenever the request specifies them.

Every component `id` and Button action event `name` MUST match
`^[A-Za-z_][A-Za-z0-9_]*$`. Use snake_case only: no spaces, hyphens, punctuation,
or IDs beginning with a digit.
Do not include HTML, JavaScript, URLs, markdown links, remote images, or unsupported properties.

Set persistent=true only for a cross-scene HUD or tracker that should remain useful after the scene
image changes, such as health, inventory, currency, objectives, or status. Scene-specific choices,
object interactions, clues, and flavor cards must use persistent=false."""

    def _generate(self, prompt: str, image_bytes: Optional[bytes], mime_type: str) -> A2UISurfaceBatchDraft:
        logger.debug(
            "%s Dispatching UI generation theater=%s model=%s prompt_chars=%d image_bytes=%d mime=%s backend=%s",
            LOG_PREFIX,
            self.active_theater_id or "default",
            self.model,
            len(prompt),
            len(image_bytes or b""),
            mime_type,
            type(self.text_response_provider).__name__ if self.text_response_provider else "unconfigured",
        )
        provider = self.text_response_provider
        if provider is None:
            raise RuntimeError("The A2UI generator is not configured.")
        response = provider.generate(TextResponseRequest(
            prompt=prompt,
            model=self.model,
            temperature=0.7,
            max_output_tokens=4096,
            # Vertex's responseSchema path is supported by the configured
            # Gemini model; the provider preserves this JSON Schema for local
            # validation after the SDK normalizes a copy for the API.
            response_schema=self.response_schema,
            attachments=(TextResponseAttachment(data=image_bytes, mime_type=mime_type),) if image_bytes else (),
        ))
        parsed = response.parsed or json.loads(response.text)
        # Accept a legacy single-surface response from test doubles and older
        # providers, while constrained generation emits the batch envelope.
        if isinstance(parsed, A2UISurfaceDraft):
            parsed = {"surfaces": [parsed]}
        elif isinstance(parsed, dict) and "surfaces" not in parsed and "components" in parsed:
            parsed = {"surfaces": [parsed]}
        draft = parsed if isinstance(parsed, A2UISurfaceBatchDraft) else A2UISurfaceBatchDraft.model_validate(parsed)
        logger.debug(
            "%s Provider generation completed provider=%s model=%s request_id=%s usage=%s",
            LOG_PREFIX,
            response.provider,
            response.model,
            response.request_id,
            dict(response.usage),
        )
        self._log_generated_draft(draft)
        return draft

    @staticmethod
    def _repair_prompt(prompt: str, error: Exception) -> str:
        return (
            f"{prompt}\n\nYour previous draft passed JSON formatting but failed the trusted Canvas Catalog "
            f"validator: {error}\nReturn a complete corrected A2UISurfaceBatchDraft. Do not omit any "
            "component-specific required property. In particular, every Card needs `child`; every Column, "
            "Row, and Grid needs `children`; and each referenced ID must exist in the returned components."
        )

    @staticmethod
    def _log_generated_draft(draft: A2UISurfaceBatchDraft) -> None:
        if not logger.isEnabledFor(logging.DEBUG):
            return
        logger.debug(
            "%s Generated draft %s",
            LOG_PREFIX,
            json.dumps(draft.model_dump(), ensure_ascii=False, default=str)[:20_000],
        )

    @staticmethod
    def _validate_components(components: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not components or len(components) > MAX_COMPONENTS:
            raise ValueError(f"A surface must contain 1-{MAX_COMPONENTS} components.")
        clean = json.loads(json.dumps(components))
        id_map: dict[str, str] = {}
        normalized_ids: set[str] = set()
        for index, component in enumerate(clean):
            if not isinstance(component, dict):
                raise ValueError(f"Component at index {index} must be an object.")
            raw_id = component.get("id")
            if not isinstance(raw_id, str) or not raw_id.strip():
                raise ValueError(
                    f"Component at index {index} is missing a non-empty string id (received {raw_id!r})."
                )
            normalized_id = InteractiveCanvasTools._canonical_identifier(raw_id, prefix="component")
            if normalized_id in normalized_ids:
                raise ValueError(
                    f"Component id {raw_id!r} collides with another id after normalization to {normalized_id!r}."
                )
            id_map[raw_id] = normalized_id
            normalized_ids.add(normalized_id)
            component["id"] = normalized_id

        # The Vertex-safe flattened projection requires primitive fields that
        # are conditional in the real discriminated catalog. Remove fields
        # not applicable to the selected component before strict validation.
        for component in clean:
            allowed = COMPONENT_ALLOWED_FIELDS.get(component.get("component"))
            if allowed is not None:
                for key in list(component):
                    if key not in allowed:
                        component.pop(key)

        # Repair references in the same pass as IDs so a model-generated
        # `health-bar` remains connected after becoming `health_bar`.
        for component in clean:
            if isinstance(component.get("child"), str):
                component["child"] = id_map.get(component["child"], component["child"])
            if isinstance(component.get("children"), list):
                component["children"] = [
                    id_map.get(child, child) if isinstance(child, str) else child
                    for child in component["children"]
                ]

        remapped = {raw: normalized for raw, normalized in id_map.items() if raw != normalized}
        if remapped:
            logger.debug("%s Normalized generated component IDs: %s", LOG_PREFIX, remapped)

        ids: set[str] = set()
        for index, component in enumerate(clean):
            component_id = component.get("id")
            kind = component.get("component")
            if not isinstance(component_id, str) or not _IDENTIFIER.fullmatch(component_id):
                raise ValueError(
                    f"Component at index {index} has invalid id {component_id!r}; expected snake_case identifier."
                )
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
                if not isinstance(event, dict) or not str(event.get("name", "")).strip():
                    raise ValueError(f"Button {component_id} needs a safe action.event name.")
                raw_event_name = str(event["name"])
                event["name"] = InteractiveCanvasTools._canonical_identifier(
                    raw_event_name, prefix="action"
                )
                if raw_event_name != event["name"]:
                    logger.debug(
                        "%s Normalized action name button=%s from=%r to=%r",
                        LOG_PREFIX,
                        component_id,
                        raw_event_name,
                        event["name"],
                    )
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
            if any(not isinstance(ref, str) or ref not in ids for ref in refs):
                raise ValueError(f"Component {component['id']} references an unknown child.")
        for index, component in enumerate(clean):
            errors = sorted(CANVAS_COMPONENT_VALIDATOR.iter_errors(component), key=lambda item: list(item.path))
            if errors:
                detail = errors[0].message
                raise ValueError(f"Component at index {index} violates the Canvas Catalog: {detail}")
        return clean

    @staticmethod
    def _canonical_identifier(value: str, prefix: str) -> str:
        """Repair common LLM identifier mistakes into the trusted ASCII subset."""
        value = str(value).strip()
        if _IDENTIFIER.fullmatch(value):
            return value
        normalized = re.sub(r"[^A-Za-z0-9_]", "_", value)
        normalized = re.sub(r"_+", "_", normalized).strip("_")
        if not normalized:
            raise ValueError(f"Identifier {value!r} cannot be normalized safely.")
        if normalized[0].isdigit():
            normalized = f"{prefix}_{normalized}"
        if not _IDENTIFIER.fullmatch(normalized):
            raise ValueError(f"Identifier {value!r} cannot be normalized safely.")
        return normalized

    @single_flight(timeout=30.0, error_message="An interactive canvas design is already in progress.")
    @with_cooldown("updating the interactive canvas")
    def update_interactive_canvas(self, request: str) -> dict[str, Any]:
        """Ask the UI agent to add new UI or update the relevant current surface.

        Args:
            request: The UI content or state change to represent on the current canvas.
        """
        if not request:
            return {"error": "Describe the UI or update to apply."}
        logger.debug(
            "%s Update requested theater=%s request=%r",
            LOG_PREFIX,
            self.active_theater_id or "default",
            request,
        )
        canvas = self._canvas()
        context, image_bytes, mime_type = self._canvas_context()
        prompt = self._prompt(request, context, has_canvas_image=bool(image_bytes))
        draft: A2UISurfaceBatchDraft | None = None
        validated_drafts: list[tuple[A2UISurfaceDraft, list[dict[str, Any]]]] | None = None
        for attempt in range(2):
            try:
                draft = self._generate(prompt, image_bytes, mime_type)
                validated_drafts = [
                    (surface_draft, self._validate_components(surface_draft.components))
                    for surface_draft in draft.surfaces
                ]
                target_ids = [str(surface.target_surface_id or "").strip() for surface, _ in validated_drafts]
                specified_ids = [target_id for target_id in target_ids if target_id]
                if len(specified_ids) != len(set(specified_ids)):
                    raise ValueError("Each existing target_surface_id may appear at most once in a batch.")
                if len(validated_drafts) > self.max_surfaces:
                    raise ValueError(f"A batch may contain at most {self.max_surfaces} surfaces.")
                break
            except ValueError as exc:
                if attempt:
                    logger.warning("%s Canvas generation failed: %s", LOG_PREFIX, exc)
                    return {"error": f"Could not update the interactive canvas: {exc}"}
                logger.info("%s Retrying invalid catalog draft: %s", LOG_PREFIX, exc)
                prompt = self._repair_prompt(prompt, exc)
            except Exception as exc:
                logger.warning("%s Canvas generation failed: %s", LOG_PREFIX, exc)
                return {"error": f"Could not update the interactive canvas: {exc}"}
        if draft is None or validated_drafts is None:
            return {"error": "Could not update the interactive canvas."}
        logger.debug(
            "%s Draft validated batch surfaces=%d targets=%s",
            LOG_PREFIX,
            len(validated_drafts),
            [surface.target_surface_id or "<new>" for surface, _ in validated_drafts],
        )

        # Build and validate the entire mutation set before touching canvas
        # state, so one invalid/stale surface cannot leave a partial batch.
        prepared: list[tuple[dict[str, Any], bool]] = []
        for surface_draft, components in validated_drafts:
            target_surface_id = str(surface_draft.target_surface_id or "").strip()[:100]
            existing = canvas.interactive_surfaces.get(target_surface_id) if target_surface_id else None
            if target_surface_id and not existing:
                return {"error": "The UI agent selected a surface that is no longer active."}
            if not existing:
                surface_id = f"canvas_{uuid.uuid4().hex}"
                surface = {
                    "surface_id": surface_id,
                    "persistent": bool(surface_draft.persistent),
                    "placement": {
                        "left_pct": round(surface_draft.left_pct, 2),
                        "top_pct": round(surface_draft.top_pct, 2),
                        "width_pct": round(surface_draft.width_pct, 2),
                    },
                    "messages": [{
                        "version": A2UI_VERSION,
                        "createSurface": {
                            "surfaceId": surface_id,
                            "catalogId": CANVAS_CATALOG_ID,
                            "sendDataModel": True,
                            "components": components,
                            "dataModel": surface_draft.data_model,
                        },
                    }],
                }
                prepared.append((surface, True))
                continue
            updated = json.loads(json.dumps(existing))
            create_message = next((message for message in updated.get("messages", []) if message.get("createSurface")), None)
            if create_message is None:
                return {"error": "The existing surface has no A2UI createSurface message."}
            updated["messages"] = [
                create_message,
                {"version": A2UI_VERSION, "updateComponents": {"surfaceId": target_surface_id, "components": components}},
                {"version": A2UI_VERSION, "updateDataModel": {"surfaceId": target_surface_id, "path": "/", "value": surface_draft.data_model}},
            ]
            updated["persistent"] = bool(existing.get("persistent", False) or surface_draft.persistent)
            prepared.append((updated, False))

        for surface, _ in prepared:
            canvas.upsert_interactive_surface(surface, max_surfaces=self.max_surfaces)
        created = [surface["surface_id"] for surface, is_new in prepared if is_new]
        updated = [surface["surface_id"] for surface, is_new in prepared if not is_new]
        for surface, is_new in prepared:
            logger.info(
                "%s %s surface theater=%s surface=%s components=%d placement=%s",
                LOG_PREFIX,
                "Created" if is_new else "Updated",
                self.active_theater_id or "default",
                surface["surface_id"],
                len(next(message["createSurface"]["components"] for message in surface["messages"] if "createSurface" in message))
                if is_new else len(surface["messages"][1]["updateComponents"]["components"]),
                surface.get("placement"),
            )
        status = "displayed" if created and not updated else "updated" if updated and not created else "applied"
        return {
            "status": status,
            "surface_id": (created + updated)[0],
            "surface_ids": created + updated,
            "surface_count": len(prepared),
            "component_count": sum(len(components) for _, components in validated_drafts),
            # Preserve the legacy single-surface summary for existing callers.
            "persistent": prepared[0][0]["persistent"] if len(prepared) == 1 else None,
        }

    @logged_tool_call
    def clear_interactive_canvas(self) -> dict[str, Any]:
        """Remove all generated A2UI surfaces from the current canvas."""
        removed = self._canvas().delete_interactive_surface("all")
        logger.info(
            "%s Cleared surfaces theater=%s removed=%s",
            LOG_PREFIX,
            self.active_theater_id or "default",
            removed,
        )
        return {"status": "cleared", "removed": removed}
