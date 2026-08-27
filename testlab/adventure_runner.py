"""Local adventure runner for testing narrative consistency with real story planning and mocked peripherals.

Usage:
    # Interactive CLI mode:
    python testlab/adventure_runner.py --adventure groove-space-odyssey

    # Non-interactive smoke test:
    python testlab/adventure_runner.py --adventure groove-space-odyssey --smoke
"""

from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import shutil
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
import uuid

from dotenv import load_dotenv
from jinja2 import StrictUndefined, Template
from google.adk.agents import Agent
from google.adk.apps.app import App
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from components.canvas_state_service import CanvasStateService
from components.theater_manager import TheaterManager
from services.agent import AGENT_INSTRUCTION_TEMPLATE, get_playlists_context, get_references_context
from tools.story_planning_tool import StoryPlanningTools, VertexGemini
from tools.tool_bundle import ToolBundle
from providers import get_text_response_provider
from utils.config_loader import deep_merge, get_app_config, get_theater_default_config

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent
ADVENTURES_DIR = ROOT_DIR / "adventures"
THEATERS_DIR = ROOT_DIR / "theaters"
load_dotenv(ROOT_DIR / ".env")


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

    def create_triframe(
        self,
        base_frame: str,
        second_frame_change: str,
        third_frame_change: str,
        reference_images: List[str] | str | None = None,
    ) -> Dict[str, Any]:
        """Creates a looping triframe animation."""
        anim_id = f"anim_{uuid.uuid4().hex[:6]}"
        result = {"animation_id": anim_id, "status": "ready"}
        return self._record(
            "create_triframe",
            {
                "base_frame": base_frame,
                "second_frame_change": second_frame_change,
                "third_frame_change": third_frame_change,
                "reference_images": reference_images,
            },
            result,
        )

    def play_animation(self, animation_id: str) -> str:
        """Plays a ready triframe animation on the canvas."""
        self.canvas_state.active_animation = animation_id
        result = f"Playing animation '{animation_id}' on canvas."
        return self._record("play_animation", {"animation_id": animation_id}, result)

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
    for key in ("agent_internal", "image_generation", "story_planning", "interactive_canvas", "music"):
        if key in app_config:
            deep_merge(config.setdefault(key, {}), app_config[key])

    return config, adv_path, adv_id


class AdventureSession:
    """Manages an isolated text-agent session running an adventure with real StoryPlanningTools."""

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
            self.config.setdefault("story_planning", {})["planner_model"] = self.planner_model_override
        if self.nodes_ahead_override is not None:
            self.config.setdefault("story_planning", {})["nodes_ahead"] = self.nodes_ahead_override

        self._populate_theater_workspace()

        self.canvas_state_service = CanvasStateService(self.theater_manager)

        # Initialize the real StoryPlanningTools
        sp_config = self.config.get("story_planning", {})
        planner_model_name = str(sp_config.get("planner_model", "gemini-3.7-flash"))
        story_planning_text_provider = get_text_response_provider(
            str(sp_config.get("text_provider", "gemini-3")),
            {"model": planner_model_name},
        )
        self.story_planning_tools = StoryPlanningTools(
            config=sp_config,
            theater_id=self.session_id,
            canvas_state_service=self.canvas_state_service,
            theater_manager=self.theater_manager,
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

    def _process_user_action_wrapper(self, user_action: str, nudge: str = "") -> Dict[str, Any]:
        """Wrapper for process_user_action that resolves synchronously and delivers the planner result."""
        clean_action = str(user_action or "").strip()
        clean_nudge = str(nudge or "").strip()
        logger.info("[AdventureRunner] process_user_action called: action=%r, nudge=%r", clean_action, clean_nudge)

        # Run in a dedicated worker thread so that the internal asyncio.run() in
        # StoryPlanningTools._run_planner_agent does not collide with the ADK agent's event loop.
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                self.story_planning_tools._resolve_user_action,
                clean_action,
                nudge=clean_nudge,
            )
            result = future.result()

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
            tools.extend([
                self.story_planning_tools.sticky_note,
                self.story_planning_tools.clear_scene,
            ])

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
                self.mock_tools.create_triframe,
                self.mock_tools.play_animation,
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
        for call in new_tool_calls:
            if call.get("tool") == "process_user_action" and isinstance(call.get("result"), dict):
                lore_activity.extend(call["result"].get("lore_activity", []))
                lore_docs_browsed.extend(call["result"].get("lore_docs_browsed", []))

        lore_docs_browsed = list(dict.fromkeys(lore_docs_browsed))

        history_item = {
            "turn_index": len(self.history) + 1,
            "timestamp": turn_start_time,
            "user_message": clean_input,
            "agent_response": turn_result["text"],
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
            "sticky_notes": self.story_planning_tools.get_present_sticky_notes(),
            "characters": self.story_planning_tools.get_present_characters(),
            "plot_beats": self.story_planning_tools.get_plot_beats(),
            "last_scene_reaction": dict(getattr(self.story_planning_tools, "_last_scene_reaction", {})),
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


# ==============================================================================
# CLI Entry Point
# ==============================================================================

def main() -> int:
    parser = argparse.ArgumentParser(description="Local Adventure Runner for narrative consistency testing.")
    parser.add_argument("--adventure", default="groove-space-odyssey", help="Adventure folder name or path.")
    parser.add_argument("--agent-model", default="gemini-3.7-flash", help="Model ID for text agent.")
    parser.add_argument("--planner-model", default="gemini-3.7-flash", help="Model ID for story planner.")
    parser.add_argument("--nodes", type=int, default=3, help="Nodes ahead buffer.")
    parser.add_argument("--smoke", action="store_true", help="Run a single automated smoke action and exit.")
    parser.add_argument("--action", default="", help="Optional single action to execute.")
    args = parser.parse_args()

    print(f"\n========================================================")
    print(f"  NARRATRON LOCAL ADVENTURE RUNNER")
    print(f"  Adventure: {args.adventure}")
    print(f"  Agent Model: {args.agent_model} | Planner: {args.planner_model}")
    print(f"========================================================\n")

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

    if args.smoke:
        action = args.action or "I power up the synthesizer console and check our navigation coordinates."
        print(f"\n[Smoke Test] Sending action: {action!r}\n")
        res = session.send_message(action)
        if "error" in res:
            print(f"Smoke test failed: {res['error']}")
            session.cleanup()
            return 1
        turn = res["turn"]
        print(f"--- Agent Response ---")
        print(turn["agent_response"])
        print(f"\n--- Tool Calls ({len(turn['tool_calls'])}) ---")
        for tc in turn["tool_calls"]:
            print(f"  [{tc['tool']}] -> {tc['result']}")
        print(f"\n--- Resulting Plot Beats ---")
        for beat in res["state"]["plot_beats"]:
            print(f"  [Beat] {beat.get('plot_beat')}")
        session.cleanup()
        return 0

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
            print(f"\nNarratron > {turn['agent_response']}\n")
            if turn["tool_calls"]:
                print("  [Peripherals Staged]:")
                for tc in turn["tool_calls"]:
                    if tc["tool"] != "process_user_action":
                        print(f"    * {tc['tool']}: {tc['result']}")
            print()
    finally:
        session.cleanup()

    print("\nSession ended.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
