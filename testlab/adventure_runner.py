"""Local adventure runner for testing narrative consistency with real story planning and mocked peripherals.

Usage:
    # Interactive CLI mode:
    python testlab/adventure_runner.py --adventure example_adventure

    # Non-interactive smoke test:
    python testlab/adventure_runner.py --adventure example_adventure --smoke

    # Autonomous player mode (Autoplay):
    python testlab/adventure_runner.py --adventure example_adventure --autoplay --turns 10

    # Autoplay with custom directives and persona:
    python testlab/adventure_runner.py --adventure example_adventure --autoplay --turns 20 \
        --autoplay-instructions "Play in a realistic style: cautious, pragmatic mortal assistant prioritizing survival."
"""

from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
import uuid

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ADVENTURES_DIR = ROOT_DIR / "adventures"
THEATERS_DIR = ROOT_DIR / "theaters"
load_dotenv(ROOT_DIR / ".env")

from jinja2 import StrictUndefined, Template
from google.adk.agents import Agent
from google.adk.apps.app import App
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import BaseModel, Field

from components.canvas.canvas_state_service import CanvasStateService
from components.theater_manager import TheaterManager
from services.live_agent import AGENT_INSTRUCTION_TEMPLATE, get_playlists_context, get_references_context
from tools.story import StoryTool, VertexGemini
from tools.tool_bundle import ToolBundle
from providers import get_text_response_provider
from providers.text_response_provider import TextResponseRequest
from utils.config_loader import (
    deep_merge,
    get_app_config,
    get_theater_default_config,
    save_theater_config,
)

logger = logging.getLogger(__name__)


class MockCanvasState:
    """Tracks mock visual, audio, thought, and interactive UI state for testing."""

    def __init__(self) -> None:
        self.current_image: Optional[str] = None
        self.current_image_prompt: Optional[str] = None
        self.current_image_effect: Optional[str] = None
        self.current_music: Optional[str] = None
        self.music_status: str = "stopped"
        self.current_thought: Optional[str] = None
        self.last_interactive_canvas_request: Optional[str] = None
        self.active_animation: Optional[str] = None
        self.tool_logs: List[Dict[str, Any]] = []

    def log_call(self, tool_name: str, args: Dict[str, Any], result: Any) -> None:
        """Record a tool invocation in the chronological trace."""
        entry = {
            "timestamp": time.time(),
            "iso_time": datetime.now(timezone.utc).isoformat(),
            "tool": tool_name,
            "args": args,
            "result": result,
        }
        self.tool_logs.append(entry)
        logger.debug("[MockToolCall] %s -> %s", tool_name, result)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "current_image": self.current_image,
            "current_image_prompt": self.current_image_prompt,
            "current_image_effect": self.current_image_effect,
            "current_music": self.current_music,
            "music_status": self.music_status,
            "current_thought": self.current_thought,
            "last_interactive_canvas_request": self.last_interactive_canvas_request,
            "active_animation": self.active_animation,
            "tool_logs_count": len(self.tool_logs),
        }


class MockToolBundle:
    """Mock implementations of peripheral theater tools that log calls and update mock canvas state."""

    def __init__(
        self,
        canvas_state: MockCanvasState,
        available_references: Optional[List[Dict[str, str]]] = None,
        available_playlists: Optional[Dict[str, List[str]]] = None,
        on_tool_call: Optional[Callable[[str, Dict[str, Any], Any], None]] = None,
    ) -> None:
        self.canvas_state = canvas_state
        self.references = available_references or []
        self.playlists = available_playlists or {}
        self.on_tool_call = on_tool_call
        self._created_images: List[str] = []

    def _record(self, tool_name: str, args: Dict[str, Any], result: Any) -> Any:
        self.canvas_state.log_call(tool_name, args, result)
        if self.on_tool_call:
            try:
                self.on_tool_call(tool_name, args, result)
            except Exception as e:
                logger.warning("Error in tool call callback: %s", e)
        return result

    # --- Image Tools ---

    def list_references(self) -> List[Dict[str, str]]:
        """List preloaded reference images from the session references directory."""
        return self._record("list_references", {}, list(self.references))

    def create_image(
        self,
        image_prompt: str,
        image_name: str,
        reference_images: List[str] | str | None = None,
        display: bool = True,
        effect: str = "gleam3",
    ) -> str:
        """Creates an image based on a prompt and adapts visual style using reference images."""
        self._created_images.append(image_name)
        if display:
            self.canvas_state.current_image = image_name
            self.canvas_state.current_image_prompt = image_prompt
            self.canvas_state.current_image_effect = effect
        result = f"Created and staged image '{image_name}' with effect '{effect}'."
        return self._record(
            "create_image",
            {
                "image_prompt": image_prompt,
                "image_name": image_name,
                "reference_images": reference_images,
                "display": display,
                "effect": effect,
            },
            result,
        )

    def show_image(
        self,
        file_path_or_name: str,
        transition: str = "crossfade",
        effect: str = "gleam3",
    ) -> str:
        """Shows an image on the canvas."""
        self.canvas_state.current_image = file_path_or_name
        self.canvas_state.current_image_effect = effect
        result = f"Displaying image '{file_path_or_name}' (transition={transition}, effect={effect})."
        return self._record(
            "show_image",
            {"file_path_or_name": file_path_or_name, "transition": transition, "effect": effect},
            result,
        )

    def browse_images(self) -> List[str]:
        """Returns a list of all available generated image file paths."""
        return self._record("browse_images", {}, list(self._created_images))

    def search_image_by_metadata(self, metadata_query: str) -> List[str]:
        """Returns a list of image file paths whose metadata matches the query."""
        q = metadata_query.lower()
        matches = [img for img in self._created_images if q in img.lower()]
        return self._record("search_image_by_metadata", {"metadata_query": metadata_query}, matches)

    # --- Chat Tools ---

    def send_chat_message(self, text: str) -> str:
        """Updates the pinned Narratron's current thought panel above chat."""
        self.canvas_state.current_thought = text
        result = f"Updated thought panel: {text}"
        return self._record("send_chat_message", {"text": text}, result)

    # --- Music Tools ---

    def play_music(self, music_id: str) -> str:
        """Choose music or a playlist to play on the canvas."""
        self.canvas_state.current_music = music_id
        self.canvas_state.music_status = "playing"
        result = f"Playing music track/playlist '{music_id}'."
        return self._record("play_music", {"music_id": music_id}, result)

    def pause_music(self) -> str:
        """Pause the current music track or playlist."""
        self.canvas_state.music_status = "paused"
        result = "Music paused."
        return self._record("pause_music", {}, result)

    def resume_music(self) -> str:
        """Resume the paused music track or playlist."""
        self.canvas_state.music_status = "playing"
        result = "Music resumed."
        return self._record("resume_music", {}, result)

    def create_music(self, prompt: str, handle: str = "") -> str:
        """Generate custom background music and play it on the canvas."""
        music_id = handle or f"gen_track_{len(self.canvas_state.tool_logs) + 1}"
        self.canvas_state.current_music = music_id
        self.canvas_state.music_status = "playing"
        result = f"Generated and playing custom track '{music_id}' for prompt: {prompt}."
        return self._record("create_music", {"prompt": prompt, "handle": handle}, result)

    # --- Interactive Canvas (A2UI) Tools ---

    def update_interactive_canvas(self, request: str) -> str:
        """Ask the canvas-aware A2UI designer to add or update UI for the current state."""
        self.canvas_state.last_interactive_canvas_request = request
        result = f"Interactive canvas updated with request: {request}"
        return self._record("update_interactive_canvas", {"request": request}, result)

    def clear_interactive_canvas(self) -> str:
        """Remove all surfaces when the UI should be reset completely."""
        self.canvas_state.last_interactive_canvas_request = None
        result = "Interactive canvas cleared."
        return self._record("clear_interactive_canvas", {}, result)

    # --- Animation Tools ---

    def create_animation(
        self,
        scene_prompt: str,
        animation_name: str,
        reference_images: List[str] | str | None = None,
    ) -> Dict[str, Any]:
        """Creates an animation."""
        anim_id = f"anim_{uuid.uuid4().hex[:6]}"
        result = {"animation_id": anim_id, "status": "ready"}
        return self._record(
            "create_animation",
            {
                "scene_prompt": scene_prompt,
                "animation_name": animation_name,
                "reference_images": reference_images,
            },
            result,
        )

    def play_animation(self, animation_id: str) -> str:
        """Plays a ready triframe animation on the canvas."""
        self.canvas_state.active_animation = animation_id
        result = f"Playing animation '{animation_id}' on canvas."
        return self._record("play_animation", {"animation_id": animation_id}, result)

    def browse_animations(self) -> List[Dict[str, Any]]:
        """Browse saved animations."""
        return self._record("browse_animations", {}, [])

    # --- Observability Tools ---

    def request_canvas_observability(self) -> str:
        """Requests current visual and interactive status of the canvas."""
        result = f"Canvas observability snapshot: image={self.canvas_state.current_image}, music={self.canvas_state.current_music} ({self.canvas_state.music_status}), thought={self.canvas_state.current_thought}"
        return self._record("request_canvas_observability", {}, result)


def list_available_adventures(adventures_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Scan adventures directory and return metadata for all valid adventures."""
    adv_dir = adventures_dir or ADVENTURES_DIR
    if not adv_dir.is_dir():
        return []

    adventures: List[Dict[str, Any]] = []
    for item in sorted(adv_dir.iterdir()):
        if not item.is_dir() or item.name.startswith("."):
            continue
        yaml_path = item / "theater.yaml"
        meta_path = item / "metadata.json"
        meta: Dict[str, Any] = {}
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning("Error reading metadata.json for %s: %s", item.name, e)

        title = meta.get("title") or item.name.replace("-", " ").title()
        description = meta.get("description") or f"Adventure package for {item.name}."
        lore_files = list(item.glob("lore/**/*.txt"))
        reference_files = [
            f for f in list(item.glob("references/**/*")) + list(item.glob("reference_library/**/*"))
            if f.is_file() and f.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
        ]
        playlist_tracks = [
            f for f in item.glob("playlists/**/*")
            if f.is_file() and f.suffix.lower() in {".mp3", ".wav", ".ogg", ".flac", ".m4a"}
        ]

        adventures.append({
            "id": meta.get("id") or item.name,
            "path": str(item.resolve()),
            "title": title,
            "description": description,
            "genre": meta.get("genre", "Interactive Adventure"),
            "tags": meta.get("tags", []),
            "has_theater_yaml": yaml_path.is_file(),
            "lore_count": len(lore_files),
            "reference_count": len(reference_files),
            "track_count": len(playlist_tracks),
            "created_at": meta.get("created_at") or datetime.fromtimestamp(item.stat().st_ctime, timezone.utc).isoformat(),
        })
    return adventures


def load_adventure_config(adventure_id_or_path: str) -> Tuple[Dict[str, Any], Path, str]:
    """Resolve adventure directory and load merged configuration."""
    adv_path = Path(adventure_id_or_path)
    if not adv_path.is_dir():
        adv_path = ADVENTURES_DIR / adventure_id_or_path

    if not adv_path.is_dir():
        raise FileNotFoundError(f"Adventure directory not found: {adventure_id_or_path}")

    adv_id = adv_path.name
    import yaml

    config = get_theater_default_config()
    yaml_path = adv_path / "theater.yaml"
    if yaml_path.is_file():
        try:
            with open(yaml_path, "r", encoding="utf-8") as f:
                disk_config = yaml.safe_load(f) or {}
                if disk_config:
                    deep_merge(config, disk_config)
        except Exception as e:
            logger.warning("Failed to load %s: %s", yaml_path, e)

    app_config = get_app_config()
    for key in ("agent_internal", "visuals", "image_generation", "story_planning", "interactive_canvas", "music"):
        if key in app_config:
            deep_merge(config.setdefault(key, {}), app_config[key])

    return config, adv_path, adv_id


class AdventureSession:
    """Manages an isolated text-agent session running an adventure with a real StoryTool."""

    def __init__(
        self,
        adventure_id_or_path: str,
        agent_model: Optional[str] = None,
        planner_model: Optional[str] = None,
        nodes_ahead: Optional[int] = None,
        session_id: Optional[str] = None,
    ) -> None:
        self.adventure_id_or_path = adventure_id_or_path
        self.agent_model_override = agent_model
        self.planner_model_override = planner_model
        self.nodes_ahead_override = nodes_ahead
        self.session_id = session_id or f"adv_runner_{Path(adventure_id_or_path).name}_{uuid.uuid4().hex[:8]}"
        self.created_at = time.time()

        self.mock_canvas = MockCanvasState()
        self.history: List[Dict[str, Any]] = []

        # Setup TheaterManager workspace populated from adventure lore and references
        self.theaters_root = THEATERS_DIR / f"_temp_runner_{self.session_id}"
        self.theater_manager = TheaterManager(base_theaters_dir=self.theaters_root)

        self._init_components()

    def _init_components(self) -> None:
        """Load configs from disk, populate workspace, and build tools and agents."""
        self.config, self.adventure_path, self.adventure_id = load_adventure_config(self.adventure_id_or_path)

        if self.agent_model_override:
            self.config.setdefault("agent_internal", {})["model"] = self.agent_model_override
            self.config.setdefault("agent", {})["model_id"] = self.agent_model_override
        if self.planner_model_override:
            story_config = self.config.setdefault("story_planning", {})
            story_config["responder_model"] = self.planner_model_override
            story_config.setdefault("deep_planning", {})["model"] = self.planner_model_override

        self._populate_theater_workspace()

        self.canvas_state_service = CanvasStateService(self.theater_manager)

        # Initialize the real StoryTool
        sp_config = self.config.get("story_planning", {})
        planner_model_name = str(
            sp_config.get("responder_model")
            or sp_config.get("model")
            or "gemini-3.7-flash"
        )
        story_planning_text_provider = get_text_response_provider(
            str(sp_config.get("text_provider", "gemini-3")),
            {"model": planner_model_name},
        )
        self.story_tool = StoryTool(
            self.theater_manager.theater(self.session_id),
            canvas_manager=self.canvas_state_service.get(self.session_id),
            text_response_provider=story_planning_text_provider,
        )

        # Build mock peripheral tool bundle
        references = self.theater_manager.get_theater_references(self.session_id)
        playlists = self.theater_manager.get_theater_playlists(self.session_id)
        self.mock_tools = MockToolBundle(
            canvas_state=self.mock_canvas,
            available_references=references,
            available_playlists=playlists,
        )

        # Build tools catalog matching services/agent.py
        self.tools = self._build_tool_catalog()

        # Build ADK Agent & Runner
        self.agent = self._create_agent()
        self.session_service = InMemorySessionService()
        app_name = re.sub(r"[^a-zA-Z0-9_]", "_", f"adv_runner_{self.session_id}")
        self.app = App(name=app_name, root_agent=self.agent)
        self.runner = Runner(app=self.app, session_service=self.session_service, auto_create_session=True)

    def _populate_theater_workspace(self) -> None:
        """Mirror lore and reference files from adventure path into the isolated theater workspace."""
        theater = self.theater_manager.theater(self.session_id)
        lore_target = theater.lore_dir()
        ref_target = theater.references_dir()
        playlists_target = theater.playlists_dir()

        # Clean existing targets to ensure no stale or deleted files remain
        if lore_target.exists():
            shutil.rmtree(lore_target)
        if ref_target.exists():
            shutil.rmtree(ref_target)
        if playlists_target.exists():
            shutil.rmtree(playlists_target)

        lore_target.mkdir(parents=True, exist_ok=True)
        ref_target.mkdir(parents=True, exist_ok=True)
        playlists_target.mkdir(parents=True, exist_ok=True)

        adv_lore = self.adventure_path / "lore"
        if adv_lore.is_dir():
            shutil.copytree(adv_lore, lore_target, dirs_exist_ok=True)

        adv_ref = self.adventure_path / "references"
        if adv_ref.is_dir():
            shutil.copytree(adv_ref, ref_target, dirs_exist_ok=True)

        adv_ref_lib = self.adventure_path / "reference_library"
        if adv_ref_lib.is_dir():
            shutil.copytree(adv_ref_lib, ref_target, dirs_exist_ok=True)

        adv_playlists = self.adventure_path / "playlists"
        if adv_playlists.is_dir():
            shutil.copytree(adv_playlists, playlists_target, dirs_exist_ok=True)

        for name in ("planning.yaml", "planning.yml"):
            adv_planning = self.adventure_path / name
            if adv_planning.is_file():
                shutil.copy2(adv_planning, theater.directory() / "planning.yaml")
                break

        save_theater_config(self.session_id, self.config, theater_manager=self.theater_manager)

    def _process_user_action_wrapper(self, user_action: str, nudge: str = "") -> Dict[str, Any]:
        """Wrapper for process_user_action that resolves synchronously and delivers the planner result."""
        clean_action = str(user_action or "").strip()
        clean_nudge = str(nudge or "").strip()
        logger.info("[AdventureRunner] process_user_action called: action=%r, nudge=%r", clean_action, clean_nudge)

        # Run in a dedicated worker thread so that the internal asyncio.run() in
        # StoryTool._resolve_user_action may create its own event loop, so keep
        # it isolated from the ADK agent's event loop.
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                self.story_tool._resolve_user_action,
                clean_action,
                nudge=clean_nudge,
            )
            result = future.result()

        # Production narration never waits for long-horizon planning, but the
        # deterministic CLI/autoplay harness should observe the completed plan
        # and sticky projection before choosing its next action.
        deep_wait_timeout = float(
            getattr(self.story_tool, "deep_planner_timeout_seconds", 300.0)
        ) + 5.0
        if not self.story_tool.wait_for_deep_planning(
            timeout=deep_wait_timeout,
            target_turn_id=int(result.get("turn_id") or 0),
        ):
            logger.warning(
                "[AdventureRunner] Deep planner did not drain within %.1f seconds",
                deep_wait_timeout,
            )

        self.mock_canvas.log_call(
            "process_user_action",
            {"user_action": clean_action, "nudge": clean_nudge},
            result,
        )
        return {
            "status": "completed",
            "message": f"[Story Planner Result] {json.dumps(result, ensure_ascii=False)}",
            "scene_reaction": result,
        }

    def _build_tool_catalog(self) -> List[Any]:
        """Build tool bundle matching the exact catalog of services/agent.py."""
        tools = [
            self.mock_tools.list_references,
            self.mock_tools.create_image,
            self.mock_tools.show_image,
            self.mock_tools.browse_images,
            self.mock_tools.search_image_by_metadata,
            self.mock_tools.send_chat_message,
            self.mock_tools.play_music,
            self.mock_tools.pause_music,
            self.mock_tools.resume_music,
        ]

        # Story planning tool
        if bool(self.config.get("story_planning", {}).get("adventure_mode", False)):
            tools.append(self._process_user_action_wrapper)
        else:
            tools.append(self.story_tool.update_sticky_note)

        # Interactive Canvas
        if bool(self.config.get("interactive_canvas", {}).get("enabled", False)):
            tools.extend([
                self.mock_tools.update_interactive_canvas,
                self.mock_tools.clear_interactive_canvas,
            ])

        # Generated Music
        if bool(self.config.get("music", {}).get("use_generated_music", False)):
            tools.append(self.mock_tools.create_music)

        # Animation
        if bool(self.config.get("animation", {}).get("enabled", False)):
            tools.extend([
                self.mock_tools.create_animation,
                self.mock_tools.play_animation,
                self.mock_tools.browse_animations,
            ])

        # Observability
        if bool(self.config.get("observability_tool", {}).get("enabled", False)):
            tools.append(self.mock_tools.request_canvas_observability)

        return tools

    def _create_agent(self) -> Agent:
        """Create ADK Agent using AGENT_INSTRUCTION_TEMPLATE and adventure config."""
        tool_bundle = ToolBundle(self.tools)
        references = get_references_context(tool_bundle)
        if not isinstance(references, str) or not references.strip():
            references = "No preloaded reference images found."
        ref_context = "\n\n## Preloaded References Context (Loaded at Agent Init)\n" + references

        theater = self.theater_manager.theater(self.session_id)
        playlists = get_playlists_context(theater)
        if not isinstance(playlists, str) or not playlists.strip():
            playlists = "No preloaded music playlists found."
        playlist_context = "\n\n## Preloaded Music Playlists Context (Loaded at Agent Init)\n" + playlists

        special_instructions = str(self.config.get("agent", {}).get("special_instructions", "")).strip()

        instruction = Template(
            AGENT_INSTRUCTION_TEMPLATE,
            undefined=StrictUndefined,
        ).render(
            ref_context=ref_context,
            playlist_context=playlist_context,
            special_instructions=special_instructions,
            animation_enabled=bool(self.config.get("animation", {}).get("enabled", False)),
            image_generation_enabled=bool(self.config.get("image_generation", {}).get("enabled", True)),
            use_generated_music=bool(self.config.get("music", {}).get("use_generated_music", False)),
            adventure_mode=bool(self.config.get("story_planning", {}).get("adventure_mode", False)),
            interactive_canvas_enabled=bool(self.config.get("interactive_canvas", {}).get("enabled", False)),
            theater_id=self.session_id,
            theater_name=self.adventure_id,
            config=self.config,
            agent=self.config.get("agent", {}),
        ).strip()

        app_internal = get_app_config().get("agent_internal", {})
        model_id = (
            self.config.get("agent", {}).get("model_id")
            or app_internal.get("model_id")
            or app_internal.get("model", "gemini-3.7-flash")
        )

        vertex_project = (
            self.config.get("vertex_project")
            or self.config.get("gcloud", {}).get("project_id")
            or os.getenv("GOOGLE_CLOUD_PROJECT")
        )
        vertex_location = str(
            self.config.get("vertex_location") or os.getenv("GOOGLE_CLOUD_LOCATION") or "global"
        )

        return Agent(
            name="adventure_text_agent",
            model=VertexGemini(
                model=model_id,
                project_id=vertex_project,
                location=vertex_location,
            ),
            instruction=instruction,
            tools=self.tools,
        )

    def send_message(self, user_message: str) -> Dict[str, Any]:
        """Send a user text message to advance the story and collect agent thoughts, tool calls, and narration."""
        clean_input = str(user_message or "").strip()
        if not clean_input:
            return {"error": "User message cannot be empty."}

        turn_start_time = time.time()
        start_tool_log_count = len(self.mock_canvas.tool_logs)

        def _execute_turn_in_clean_thread() -> Dict[str, Any]:
            async def _run_async_turn() -> Dict[str, Any]:
                final_text_parts: List[str] = []
                async for event in self.runner.run_async(
                    user_id="player",
                    session_id=self.session_id,
                    new_message=types.Content(role="user", parts=[types.Part(text=clean_input)]),
                ):
                    if event.is_final_response() and event.content and event.content.parts:
                        for part in event.content.parts:
                            if part.text:
                                final_text_parts.append(part.text)

                final_text = "".join(final_text_parts).strip()
                return {"text": final_text}

            return asyncio.run(_run_async_turn())

        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                turn_result = executor.submit(_execute_turn_in_clean_thread).result()
        except Exception as e:
            logger.exception("[AdventureRunner] Turn execution error: %s", e)
            return {
                "error": f"Agent turn failed: {e}",
                "user_message": clean_input,
                "state": self.get_state(),
            }

        new_tool_calls = self.mock_canvas.tool_logs[start_tool_log_count:]

        lore_activity: List[Dict[str, Any]] = []
        lore_docs_browsed: List[str] = []
        narration: str = ""
        dialogue: List[Dict[str, Any]] = []

        for call in new_tool_calls:
            if call.get("tool") == "process_user_action" and isinstance(call.get("result"), dict):
                call_res = call["result"]
                lore_activity.extend(call_res.get("lore_activity", []))
                lore_docs_browsed.extend(call_res.get("lore_docs_browsed", []))
                if call_res.get("narration"):
                    narration = str(call_res["narration"]).strip()
                if isinstance(call_res.get("dialogue"), list):
                    dialogue = call_res["dialogue"]

        lore_docs_browsed = list(dict.fromkeys(lore_docs_browsed))

        # Fallback to last scene reaction if process_user_action wasn't in new_tool_calls
        if not narration or not dialogue:
            last_reaction = getattr(self.story_tool, "_last_scene_reaction", {})
            if isinstance(last_reaction, dict):
                if not narration and last_reaction.get("narration"):
                    narration = str(last_reaction["narration"]).strip()
                if not dialogue and isinstance(last_reaction.get("dialogue"), list):
                    dialogue = last_reaction["dialogue"]

        agent_text = str(turn_result.get("text") or "").strip()
        effective_narration = narration or agent_text
        effective_agent_response = agent_text or narration

        history_item = {
            "turn_index": len(self.history) + 1,
            "timestamp": turn_start_time,
            "user_message": clean_input,
            "agent_response": effective_agent_response,
            "narration": effective_narration,
            "dialogue": dialogue,
            "tool_calls": new_tool_calls,
            "lore_activity": lore_activity,
            "lore_docs_browsed": lore_docs_browsed,
        }
        self.history.append(history_item)

        return {
            "turn": history_item,
            "state": self.get_state(),
            "mock_canvas": self.mock_canvas.as_dict(),
        }

    def get_state(self) -> Dict[str, Any]:
        """Return the current narrative state snapshot."""
        return {
            "session_id": self.session_id,
            "adventure_id": self.adventure_id,
            "created_at": self.created_at,
            "sticky_notes": self.story_tool.get_present_sticky_notes(),
            "characters": self.story_tool.get_present_characters(),
            # Retain the response field for older Test Lab clients. The new
            # deep planner owns durable state through stickies, not plot beats.
            "plot_beats": [],
            "deep_plan": self.story_tool.get_deep_plan(),
            "last_scene_reaction": dict(getattr(self.story_tool, "_last_scene_reaction", {})),
            "lore_documents": self.theater_manager.get_lore_documents(self.session_id),
            "mock_canvas": self.mock_canvas.as_dict(),
            "turns_count": len(self.history),
        }

    def reset(self) -> None:
        """Reset session state, re-read configs/lore/references from disk, and re-initialize story planner and agent."""
        self._init_components()
        self.mock_canvas = MockCanvasState()
        self.history.clear()

    def cleanup(self) -> None:
        """Remove temporary session theater files."""
        if self.theaters_root.exists():
            try:
                shutil.rmtree(self.theaters_root)
            except Exception as e:
                logger.warning("Failed to clean up %s: %s", self.theaters_root, e)


def format_repl_turn(turn: Dict[str, Any]) -> str:
    """Format an adventure turn for interactive REPL display including narration, dialogue, and staged peripherals."""
    narration = str(turn.get("narration") or turn.get("agent_response") or "").strip()
    dialogue = turn.get("dialogue") or []
    tool_calls = turn.get("tool_calls") or []

    lines: List[str] = [f"Narratron > {narration}"]

    if dialogue:
        lines.append("")
        lines.append("  [Dialogue]:")
        for d in dialogue:
            speaker = str(d.get("speaker") or "NPC").strip()
            text = str(d.get("text") or "").strip()
            kind = str(d.get("kind") or "speech").lower()
            if kind == "thought":
                lines.append(f"    * {speaker} (thought): ({text})")
            else:
                lines.append(f"    * {speaker}: \"{text}\"")

    peripherals = [tc for tc in tool_calls if tc.get("tool") != "process_user_action"]
    if peripherals:
        lines.append("")
        lines.append("  [Peripherals Staged]:")
        for tc in peripherals:
            lines.append(f"    * {tc.get('tool')}: {tc.get('result')}")

    return "\n".join(lines)


# ==============================================================================
# Autonomous Player (Autoplay) Components
# ==============================================================================

class PlayerTurnDecision(BaseModel):
    """Structured decision made by an autonomous player agent."""

    thought: str = Field(
        default="",
        description="Internal strategic reasoning, tactical thought, or reflection based on player directives.",
    )
    action: str = Field(
        ...,
        description="The exact in-character action, statement, or dialogue the player performs (1-3 sentences).",
    )


class AutoPlayer:
    """Autonomous LLM player agent that simulates human player actions in an adventure."""

    DEFAULT_INSTRUCTIONS = (
        "Explore the world dynamically: investigate suspicious elements, converse with key "
        "characters, make creative choices, and advance the plot."
    )

    def __init__(
        self,
        adventure_title: str = "",
        adventure_description: str = "",
        instructions: str = "",
        model: str = "gemini-3.7-flash",
        provider_id: str = "gemini-3",
        text_provider: Optional[Any] = None,
    ) -> None:
        self.adventure_title = adventure_title or "Interactive Adventure"
        self.adventure_description = adventure_description or "An interactive text story."
        self.instructions = (
            instructions.strip() if instructions and instructions.strip() else self.DEFAULT_INSTRUCTIONS
        )
        self.model = model
        self.provider_id = provider_id
        self.text_provider = text_provider or get_text_response_provider(
            self.provider_id,
            options={"model": self.model},
        )

    def _build_system_instruction(self) -> str:
        return (
            f"You are an autonomous AI playing an interactive text adventure game titled '{self.adventure_title}'.\n"
            f"Premise: {self.adventure_description}\n\n"
            f"YOUR PLAYSTYLE DIRECTIVES & PERSONA:\n"
            f"{self.instructions}\n\n"
            f"GUIDELINES FOR PLAYING:\n"
            f"1. You are acting as the HUMAN PLAYER in this story. Speak or act as the protagonist.\n"
            f"2. Read the latest narration, scene changes, and dialogue from NPCs carefully.\n"
            f"3. Strictly adhere to your playstyle directives (e.g. if instructed to be zany and break the game, "
            f"take audacious, unexpected, rule-bending, or absurd actions; if cautious or heroic, act accordingly).\n"
            f"4. Decide on: (a) an internal 'thought' explaining your tactical reasoning or comedic intent, and "
            f"(b) a concrete in-character 'action' (1-3 sentences).\n"
            f"5. Do NOT narrate the outcome of your own action. Only state what you say or attempt to do. "
            f"The Narratron game master will determine what happens.\n"
            f"6. Do NOT prefix your action with 'Player:' or 'Action:'. Provide only the direct in-character action/speech.\n"
            f"7. If your character experiences death, disintegration, execution, or a definitive fatal loss, and play continues, acknowledge your demise and restart the adventure as a new character/candidate from the beginning (e.g. Assistant #16 or a new adventurer)."
        )

    def _build_prompt(
        self,
        session_state: Dict[str, Any],
        history: List[Dict[str, Any]],
        turn_index: int,
    ) -> str:
        sticky_notes = session_state.get("sticky_notes") or []
        sticky_summary = "\n".join(
            f"- {s.get('topic')}: {s.get('info')}" for s in sticky_notes[:8]
        ) or "None recorded yet."

        if not history:
            return (
                f"The adventure is just beginning!\n\n"
                f"Initial Clues & Setting Elements:\n"
                f"{sticky_summary}\n\n"
                f"This is Turn {turn_index}. Based on your playstyle directives, decide on your very first action "
                f"or dialogue to kick off the adventure."
            )

        # Include last 3 turns of history for immediate context
        recent_turns = history[-3:]
        history_blocks: List[str] = []
        for h in recent_turns:
            h_turn = h.get("turn_index", "?")
            h_user = h.get("user_message", "")
            h_narr = h.get("narration") or h.get("agent_response") or ""
            h_dial = h.get("dialogue") or []

            dial_lines = []
            for d in h_dial:
                spk = d.get("speaker", "NPC")
                txt = d.get("text", "")
                dial_lines.append(f"    * {spk}: \"{txt}\"")
            dial_str = "\n".join(dial_lines) if dial_lines else "    (No spoken dialogue)"

            block = (
                f"[Turn {h_turn}]\n"
                f"  Player Action: {h_user}\n"
                f"  Narratron: {h_narr}\n"
                f"  Dialogue:\n{dial_str}"
            )
            history_blocks.append(block)

        history_str = "\n\n".join(history_blocks)

        return (
            f"Recent Story Chronicle:\n"
            f"{history_str}\n\n"
            f"Current Known Clues & Environment State:\n"
            f"{sticky_summary}\n\n"
            f"This is Turn {turn_index}. Based on the latest narrative and your playstyle directives, "
            f"what do you do or say next?"
        )

    def decide_action(
        self,
        session_state: Dict[str, Any],
        history: List[Dict[str, Any]],
        turn_index: int,
    ) -> Tuple[str, str]:
        """Query the LLM to decide the player's internal thought and action."""
        system_instruction = self._build_system_instruction()
        prompt = self._build_prompt(session_state, history, turn_index)

        # Attempt structured response first
        try:
            req = TextResponseRequest(
                prompt=prompt,
                system_instruction=system_instruction,
                temperature=0.85,
                response_schema=PlayerTurnDecision,
            )
            result = self.text_provider.generate(req)
            if hasattr(result, "parsed") and isinstance(result.parsed, PlayerTurnDecision):
                thought = result.parsed.thought.strip()
                action = result.parsed.action.strip()
                action = self._clean_action(action)
                if action:
                    return thought, action
            if hasattr(result, "parsed") and isinstance(result.parsed, dict):
                thought = str(result.parsed.get("thought", "")).strip()
                action = str(result.parsed.get("action", "")).strip()
                action = self._clean_action(action)
                if action:
                    return thought, action
            raw_text = getattr(result, "text", "") or ""
            return self._parse_fallback(raw_text)
        except Exception as e:
            logger.warning("[AutoPlayer] Structured generation failed, trying raw text: %s", e)

        # Fallback to plain text generation without schema
        try:
            req = TextResponseRequest(
                prompt=prompt + "\n\nProvide your answer formatted as:\nThought: <internal thought>\nAction: <in-character action>",
                system_instruction=system_instruction,
                temperature=0.85,
            )
            result = self.text_provider.generate(req)
            raw_text = getattr(result, "text", "") or ""
            return self._parse_fallback(raw_text)
        except Exception as e:
            logger.error("[AutoPlayer] Action generation failed: %s", e)
            return "Experiencing a moment of confusion.", "I take a cautious step forward and look around carefully."

    @staticmethod
    def _clean_action(action: str) -> str:
        clean = re.sub(r"^(?:player|action)\s*[:>]\s*", "", action, flags=re.IGNORECASE).strip()
        if len(clean) >= 2 and (clean[0] == clean[-1] and clean[0] in ('"', "'")):
            clean = clean[1:-1].strip()
        return clean

    @classmethod
    def _parse_fallback(cls, text: str) -> Tuple[str, str]:
        clean = text.strip()
        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", clean, re.DOTALL)
        candidate = json_match.group(1).strip() if json_match else clean
        try:
            data = json.loads(candidate)
            if isinstance(data, dict) and "action" in data:
                return str(data.get("thought", "")).strip(), cls._clean_action(str(data["action"]))
        except Exception:
            pass

        thought = ""
        action_parts = []
        for line in clean.splitlines():
            line_str = line.strip()
            if not line_str:
                continue
            if line_str.lower().startswith("thought:"):
                thought = line_str[8:].strip()
            elif line_str.lower().startswith("action:"):
                action_parts.append(line_str[7:].strip())
            else:
                action_parts.append(line_str)

        action = " ".join(action_parts).strip() if action_parts else clean
        action = cls._clean_action(action)
        if not action:
            action = "I pause and assess my current surroundings."
        return thought, action


class AutoplayLogger:
    """Manages incremental and final logging for autoplay sessions in evaluation_results/."""

    def __init__(
        self,
        adventure_id: str,
        adventure_title: str,
        session_id: str,
        instructions: str,
        agent_model: str,
        planner_model: str,
        autoplay_model: str,
        max_turns: int,
        log_path: Optional[str | Path] = None,
    ) -> None:
        self.adventure_id = adventure_id
        self.adventure_title = adventure_title
        self.session_id = session_id
        self.instructions = instructions
        self.agent_model = agent_model
        self.planner_model = planner_model
        self.autoplay_model = autoplay_model
        self.max_turns = max_turns

        if log_path:
            self.path = Path(log_path)
        else:
            log_dir = ROOT_DIR / "evaluation_results"
            log_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.path = log_dir / f"autoplay_{adventure_id}_{timestamp}.md"

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.is_json = self.path.suffix.lower() == ".json"
        self.start_time = time.time()
        self.turns_data: List[Dict[str, Any]] = []

    def start_log(self, initial_state: Dict[str, Any]) -> None:
        """Write the initial header to the log file."""
        if self.is_json:
            self._write_json()
            return

        sticky_notes = initial_state.get("sticky_notes") or []
        sticky_lines = [f"- **{s.get('topic')}**: {s.get('info')}" for s in sticky_notes] or ["- None recorded."]
        notes_str = "\n".join(sticky_lines)

        content = [
            f"# Autoplay Session Log: {self.adventure_title}",
            "",
            f"- **Adventure ID**: `{self.adventure_id}`",
            f"- **Session ID**: `{self.session_id}`",
            f"- **Start Time**: `{datetime.now(timezone.utc).isoformat()}`",
            f"- **Autoplay Instructions**: \"{self.instructions}\"",
            f"- **Agent Model**: `{self.agent_model}`",
            f"- **Planner Model**: `{self.planner_model}`",
            f"- **Autoplay Model**: `{self.autoplay_model}`",
            f"- **Max Turns**: {self.max_turns}",
            "",
            "---",
            "",
            "## Initial Scene & Notes",
            notes_str,
            "",
            "---",
            "",
            "## Turn-by-Turn Chronicle",
            "",
        ]
        self.path.write_text("\n".join(content), encoding="utf-8")

    def log_turn(
        self,
        turn_index: int,
        thought: str,
        action: str,
        turn_result: Dict[str, Any],
        state_after: Dict[str, Any],
    ) -> None:
        """Incrementally append a completed turn to the log file."""
        turn_record = {
            "turn_index": turn_index,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "thought": thought,
            "action": action,
            "turn": turn_result,
            "plot_beats": state_after.get("plot_beats", []),
        }
        self.turns_data.append(turn_record)

        if self.is_json:
            self._write_json()
            return

        narration = turn_result.get("narration") or turn_result.get("agent_response") or ""
        dialogue = turn_result.get("dialogue") or []
        tool_calls = turn_result.get("tool_calls") or []

        dial_lines = []
        for d in dialogue:
            spk = d.get("speaker", "NPC")
            txt = d.get("text", "")
            kind = d.get("kind", "speech")
            if kind == "thought":
                dial_lines.append(f"  - *{spk} (thought)*: ({txt})")
            else:
                dial_lines.append(f"  - **{spk}**: \"{txt}\"")
        dial_str = "\n".join(dial_lines) if dial_lines else "  *(No spoken dialogue)*"

        periph_lines = []
        for tc in tool_calls:
            if tc.get("tool") != "process_user_action":
                periph_lines.append(f"  - `{tc.get('tool')}`: {tc.get('result')}")
        periph_str = "\n".join(periph_lines) if periph_lines else "  *(None)*"

        beats = state_after.get("plot_beats") or []
        beat_lines = [f"  - {b.get('plot_beat')}" for b in beats] if beats else ["  *(None)*"]
        beat_str = "\n".join(beat_lines)

        turn_md = [
            f"### Turn {turn_index}",
            f"- **Timestamp**: `{turn_record['timestamp']}`",
            f"- **Player Thought**: *{thought}*" if thought else "- **Player Thought**: *(None)*",
            f"- **Player Action**: {action}",
            "",
            "**Narratron Narration**:",
            f"> {narration}",
            "",
            "**Dialogue**:",
            dial_str,
            "",
            "**Peripherals Staged**:",
            periph_str,
            "",
            "**Active Plot Beats**:",
            beat_str,
            "",
            "---",
            "",
        ]
        with open(self.path, "a", encoding="utf-8") as f:
            f.write("\n".join(turn_md))

    def finalize(self, final_state: Dict[str, Any], interrupted: bool = False) -> None:
        """Write summary section to the log file."""
        if self.is_json:
            self._write_json(final_state=final_state, interrupted=interrupted)
            return

        elapsed = time.time() - self.start_time
        beats = final_state.get("plot_beats") or []
        beat_lines = [f"{i+1}. {b.get('plot_beat')}" for i, b in enumerate(beats)] if beats else ["- None"]
        beat_str = "\n".join(beat_lines)

        status_str = "Interrupted by user" if interrupted else "Completed successfully"
        summary_md = [
            "## Autoplay Session Summary",
            "",
            f"- **Status**: {status_str}",
            f"- **Completed Turns**: {len(self.turns_data)} / {self.max_turns}",
            f"- **Elapsed Time**: {elapsed:.1f} seconds",
            "",
            "### Ending Plot Beats",
            beat_str,
            "",
            "### Final Canvas State",
            f"```json\n{json.dumps(final_state.get('mock_canvas', {}), indent=2)}\n```",
            "",
        ]
        with open(self.path, "a", encoding="utf-8") as f:
            f.write("\n".join(summary_md))

    def _write_json(self, final_state: Optional[Dict[str, Any]] = None, interrupted: bool = False) -> None:
        payload = {
            "adventure_id": self.adventure_id,
            "adventure_title": self.adventure_title,
            "session_id": self.session_id,
            "instructions": self.instructions,
            "agent_model": self.agent_model,
            "planner_model": self.planner_model,
            "autoplay_model": self.autoplay_model,
            "max_turns": self.max_turns,
            "start_time": datetime.fromtimestamp(self.start_time, timezone.utc).isoformat(),
            "interrupted": interrupted,
            "turns": self.turns_data,
            "final_state": final_state or {},
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def run_autoplay(
    session: AdventureSession,
    instructions: str = "",
    max_turns: int = 10,
    autoplay_model: str = "gemini-3.7-flash",
    log_path: Optional[str | Path] = None,
    delay: float = 0.0,
    player: Optional[AutoPlayer] = None,
    initial_action: str = "",
) -> Dict[str, Any]:
    """Execute an autonomous play session driven by an LLM player agent."""
    adv_meta_file = session.adventure_path / "metadata.json"
    title = session.adventure_id
    description = ""
    if adv_meta_file.is_file():
        try:
            m = json.loads(adv_meta_file.read_text(encoding="utf-8"))
            title = m.get("title", title)
            description = m.get("description", description)
        except Exception:
            pass

    if player is None:
        player = AutoPlayer(
            adventure_title=title,
            adventure_description=description,
            instructions=instructions,
            model=autoplay_model,
        )

    agent_model_name = str(
        session.agent_model_override or session.config.get("agent", {}).get("model_id") or "gemini-3.7-flash"
    )
    planner_model_name = str(
        session.planner_model_override
        or session.config.get("story_planning", {}).get("planner_model")
        or "gemini-3.7-flash"
    )

    logger_inst = AutoplayLogger(
        adventure_id=session.adventure_id,
        adventure_title=title,
        session_id=session.session_id,
        instructions=player.instructions,
        agent_model=agent_model_name,
        planner_model=planner_model_name,
        autoplay_model=autoplay_model,
        max_turns=max_turns,
        log_path=log_path,
    )

    state = session.get_state()
    logger_inst.start_log(state)

    print("\n========================================================")
    print("  AUTOPLAY MODE ACTIVE")
    print(f"  Adventure: {session.adventure_id} ({title})")
    print(f"  Instructions: \"{player.instructions}\"")
    print(f"  Turns: {max_turns} | Autoplay Model: {autoplay_model}")
    print(f"  Log File: {logger_inst.path}")
    print("========================================================\n")

    interrupted = False
    try:
        for turn_idx in range(1, max_turns + 1):
            current_state = session.get_state()

            # Determine player action
            if turn_idx == 1 and initial_action:
                thought = "Using initial action supplied via command line."
                action = initial_action
            else:
                print(f"[Turn {turn_idx}/{max_turns}] AutoPlayer is deciding next action...")
                thought, action = player.decide_action(
                    session_state=current_state,
                    history=session.history,
                    turn_index=turn_idx,
                )

            print(f"\n>>> [Turn {turn_idx}/{max_turns}]")
            if thought:
                print(f"AutoPlayer [Thought]: {thought}")
            print(f"AutoPlayer > {action}")
            print("\n[Thinking & Planning...]")

            # Attempt turn with retry logic for transient API or rate limit errors
            max_turn_retries = 3
            res: Dict[str, Any] = {}
            for attempt in range(1, max_turn_retries + 1):
                res = session.send_message(action)
                if "error" not in res:
                    break
                err_msg = str(res.get("error", "Unknown error"))
                logger.warning("[Autoplay] Turn %s attempt %s failed: %s", turn_idx, attempt, err_msg)
                if attempt < max_turn_retries:
                    backoff = attempt * 4.0
                    print(f"\n[Turn {turn_idx}] Attempt {attempt} failed ({err_msg[:100]}...). Retrying in {backoff:.1f}s...")
                    time.sleep(backoff)

            if "error" in res:
                print(f"\n[Error on Turn {turn_idx}]: {res['error']}")
                logger_inst.log_turn(turn_idx, thought, action, {"error": res["error"]}, current_state)
                break

            turn = res["turn"]
            state_after = res["state"]
            print(f"\n{format_repl_turn(turn)}\n")

            logger_inst.log_turn(
                turn_index=turn_idx,
                thought=thought,
                action=action,
                turn_result=turn,
                state_after=state_after,
            )

            if delay > 0 and turn_idx < max_turns:
                time.sleep(delay)

    except (KeyboardInterrupt, EOFError):
        print("\n\n[Autoplay interrupted by user]")
        interrupted = True
    finally:
        final_state = session.get_state()
        logger_inst.finalize(final_state, interrupted=interrupted)

    print("\n========================================================")
    print("  AUTOPLAY FINISHED")
    print(f"  Completed Turns: {len(logger_inst.turns_data)} / {max_turns}")
    print(f"  Log File Saved: {logger_inst.path}")
    print("========================================================\n")

    return {
        "log_path": str(logger_inst.path),
        "turns_completed": len(logger_inst.turns_data),
        "max_turns": max_turns,
        "interrupted": interrupted,
        "final_state": final_state,
    }


# ==============================================================================
# CLI Entry Point
# ==============================================================================

def main() -> int:
    parser = argparse.ArgumentParser(description="Local Adventure Runner for narrative consistency testing.")
    parser.add_argument("--adventure", default="example_adventure", help="Adventure folder name or path.")
    parser.add_argument("--agent-model", default="gemini-3.7-flash", help="Model ID for text agent.")
    parser.add_argument("--planner-model", default="gemini-3.7-flash", help="Model ID for story planner.")
    parser.add_argument("--nodes", type=int, default=3, help="Nodes ahead buffer.")
    parser.add_argument("--smoke", action="store_true", help="Run a single automated smoke action and exit.")
    parser.add_argument("--action", default="", help="Optional single action to execute.")

    # Autoplay arguments
    parser.add_argument("--autoplay", action="store_true", help="Run in autonomous player mode.")
    parser.add_argument(
        "--autoplay-instructions",
        "--autoplay_instructions",
        dest="autoplay_instructions",
        default="",
        help="Instructions or persona directing the autonomous player (e.g. 'Be zany and try to break the game').",
    )
    parser.add_argument(
        "--turns",
        "-n",
        "--autoplay-turns",
        "--autoplay_turns",
        dest="autoplay_turns",
        type=int,
        default=10,
        help="Number of turns to execute in autoplay mode (default: 10).",
    )
    parser.add_argument(
        "--autoplay-model",
        "--autoplay_model",
        dest="autoplay_model",
        default="gemini-3.7-flash",
        help="Model ID for the autoplay player agent (default: gemini-3.7-flash).",
    )
    parser.add_argument(
        "--autoplay-log",
        "--autoplay_log",
        "--log-file",
        "--log_file",
        dest="autoplay_log",
        default="",
        help="File path to save the autoplay session log (defaults to evaluation_results/autoplay_<adventure>_<timestamp>.md).",
    )
    parser.add_argument(
        "--autoplay-delay",
        "--autoplay_delay",
        dest="autoplay_delay",
        type=float,
        default=0.0,
        help="Pause in seconds between autoplay turns (default: 0.0).",
    )

    args = parser.parse_args()

    print("\n========================================================")
    print("  NARRATRON LOCAL ADVENTURE RUNNER")
    print(f"  Adventure: {args.adventure}")
    print(f"  Agent Model: {args.agent_model} | Planner: {args.planner_model}")
    print("========================================================\n")

    try:
        session = AdventureSession(
            adventure_id_or_path=args.adventure,
            agent_model=args.agent_model,
            planner_model=args.planner_model,
            nodes_ahead=args.nodes,
        )
    except Exception as e:
        print(f"Failed to initialize adventure session: {e}")
        return 1

    state = session.get_state()
    print(f"Loaded adventure '{args.adventure}' successfully.")
    print(f"Active Sticky Notes ({len(state['sticky_notes'])}):")
    for s in state["sticky_notes"]:
        print(f"  * {s.get('topic')}: {s.get('info')}")

    # 1. Smoke test mode
    if args.smoke:
        action = args.action or "I power up the synthesizer console and check our navigation coordinates."
        print(f"\n[Smoke Test] Sending action: {action!r}\n")
        res = session.send_message(action)
        if "error" in res:
            print(f"Smoke test failed: {res['error']}")
            session.cleanup()
            return 1
        turn = res["turn"]
        print("--- Agent Response ---")
        print(turn["agent_response"])
        if turn.get("narration") and turn["narration"] != turn["agent_response"]:
            print("\n--- Narration ---")
            print(turn["narration"])
        if turn.get("dialogue"):
            print(f"\n--- Dialogue ({len(turn['dialogue'])}) ---")
            for d in turn["dialogue"]:
                speaker = str(d.get("speaker") or "NPC").strip()
                text = str(d.get("text") or "").strip()
                kind = str(d.get("kind") or "speech").lower()
                if kind == "thought":
                    print(f"  [{speaker} (thought)] ({text})")
                else:
                    print(f"  [{speaker}] \"{text}\"")
        print(f"\n--- Tool Calls ({len(turn['tool_calls'])}) ---")
        for tc in turn["tool_calls"]:
            print(f"  [{tc['tool']}] -> {tc['result']}")
        print("\n--- Resulting Plot Beats ---")
        for beat in res["state"]["plot_beats"]:
            print(f"  [Beat] {beat.get('plot_beat')}")
        session.cleanup()
        return 0

    # 2. Autoplay mode
    is_autoplay = args.autoplay or bool(args.autoplay_instructions)
    if is_autoplay:
        try:
            run_autoplay(
                session=session,
                instructions=args.autoplay_instructions,
                max_turns=args.autoplay_turns,
                autoplay_model=args.autoplay_model,
                log_path=args.autoplay_log or None,
                delay=args.autoplay_delay,
                initial_action=args.action,
            )
        finally:
            session.cleanup()
        return 0

    # 3. Interactive REPL mode
    print("\nEnter player actions below. Type 'exit', 'quit', or 'reset'.\n")
    try:
        while True:
            try:
                user_input = input("Player > ").strip()
            except (KeyboardInterrupt, EOFError):
                break

            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit"):
                break
            if user_input.lower() == "reset":
                session.reset()
                print("[Session reset to beginning]")
                continue

            print("\n[Thinking & Planning...]")
            res = session.send_message(user_input)
            if "error" in res:
                print(f"[Error]: {res['error']}\n")
                continue

            turn = res["turn"]
            print(f"\n{format_repl_turn(turn)}\n")
    finally:
        session.cleanup()

    print("\nSession ended.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

