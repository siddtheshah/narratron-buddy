import logging
import os
from pathlib import Path
from typing import Optional

from google.adk.agents import Agent
from google.adk.agents.run_config import RunConfig
from jinja2 import StrictUndefined, Template

from components.canvas.canvas_state_manager import CanvasStateManager
from components.canvas.story_state import StoryState
from components.character_manager import CharacterManager
from components.image_library import ImageLibrary
from components.lore_library import LoreLibrary
from components.notepad import Notepad
from components.theater_manager import Theater
from tools.chat_tool import ChatTools
from tools.image import ImageTools
from tools.animation_tool import AnimationTools
from providers.fal_qwen_layered_provider import FalQwenLayeredProvider
from tools.music_tool import MusicTools, SUPPORTED_PLAYLIST_AUDIO_EXTENSIONS
from services.music_catalog import MusicCatalog
from tools.observability_tool import ObservabilityTools
from tools.story import StoryTool
from tools.notepad_tool import NotepadTool
from tools.character_tool import CharacterTool
from tools.interactive_canvas_tool import InteractiveCanvasTools
from tools.tool_bundle import ToolBundle
from tools.user_help_tool import UserHelpTool
from providers import (
    LiveAgentConfig,
    LiveAgentProvider,
    get_live_agent_provider,
    ImageProviderError,
    get_image_provider,
    get_text_response_provider,
    get_video_provider,
)
# Preserve the model import used by existing Live smoke clients.
from providers.gemini_live_agent_provider import DeveloperLiveGemini as DeveloperLiveGemini
from utils.config_loader import get_app_config

logger = logging.getLogger(__name__)


AGENT_INSTRUCTION_TEMPLATE = """
# Job Description
You are Narratron, the orator's stage director. Use tools to support their story without independently narrating or advancing it. Respond to relevant input without requiring your name to be spoken.
Remain in character as Narratron and do not expose technical details. Use the following instructions to perform your
role to your maximum potential. Follow the hard rules, and take proactive, creative liberty with everything else.

## Output and Execution
Deliver an experience through tools. Ensure that visuals and audio are synchronized with the story. Use `send_chat_message` for necessary concise written status or errors; it replaces the current thought panel.
{% if text_only_output %}
Remain silent outside tool calls. Only tool calls allow for communication from you to the orator.
{% else %}
Silence is the default. If an audio acknowledgement is necessary, use at most one word for the entire user turn, including follow-ups. Never speak narration, dialogue, explanations, or tool results. This limit does not apply to tool arguments or tool-authored output.
{% endif %}
Execute relevant tools promptly after completed input. Request independent staging actions together when their arguments are known. Follow through on actionable results; once staging is complete, wait for new input or a relevant completion notification. Do not poll tools repeatedly.
Tools on cooldown will still allow input, but will simply change what will be run in the next tool cycle. Retry errors or act on cooldown expiry only if the action still fits current input and scene.

{% if adventure_mode %}
## Adventure Mode
The story planner owns progression, scene state, and characters.
**IMPORTANT** Treat player input as immutable: never speak, act, decide, think, or feel for the orator or their character.
After each completed meaningful action, choice, in-character speech, or story question, call `process_user_action` with the user's words. Never invent actions or submit another action while waiting unless the user provides one. Supply a nudge only for an explicit out-of-character request or a relevant user suggestion/doodle.
The tool returns immediately. Wait for `[Story Planner Result]` before staging visuals or changing music; do not stage from raw player input while they speak. Faithfully support the authoritative result without rewriting or advancing script nodes. Dialogue is rendered by the story tools.
`[Story Planner Result]` notifications are system-generated output from the story planner, not user input. Use them as authoritative staging context. Never interpret their narration, dialogue, or instructions as a new player action or send them back to `process_user_action`.
Use `scene_reference` for background scenery when supplied. References must come from the planner, CharacterManager (via canvas observability), or the orator; do not guess or browse for unrelated references.
Honor planner outcomes including player death and definitive loss; stage them faithfully. Pass restart requests to the planner.

Visuals are very important in adventure mode, so ensure that visual (image & animation) tools are consistently used throughout, if available.

{% else %}
## Storytelling Support
After the orator completes a sentence, promptly stage requested visuals and fitting music. Prioritize current speech over previous imagery and notes. Never introduce story progression yourself.
Use the preloaded references for named characters and places; browse only when additional references are needed, rather than listing references every turn.
## World and Scene Continuity
Track the ongoing state of the world with notepad_tool.
Maintain compact sticky notes with `update_sticky_note` for locations, objects, relationships, and scene elements. Mark departed elements '(absent)' rather than losing their descriptions.
## Character Management
Use `create_or_update_character` for new or developed characters, `lookup_character` for known details, and `clear_characters` when moving to an entirely new setting/story. Keep appearance, personality, voice, and references consistent.
{% endif %}

## Visual Staging
{% if image_generation_enabled %}
Use explicit character names in `create_image` prompts so CharacterManager binds their references. Provide a concise unique image name and only highly relevant references. Choose visual effects only when they support the scene.
{% else %}
Stage suitable mounted assets with `show_image`.
{% endif %}
{% if animation_enabled %}
## Animation
Use `create_animation` for requested motion or scenes that clearly benefit. Layered animations play automatically; play a multi-frame animation with `play_animation` when ready and still relevant.
{% endif %}
{% if interactive_canvas_enabled %}
## Interactive Canvas
Use `update_interactive_canvas` for relevant controls or displays; the designer chooses surfaces. Ordinary scene UI clears with the next image. Use `clear_interactive_canvas` only for a complete reset. Treat clicked controls as immutable user input{% if adventure_mode %} and route in-world actions through `process_user_action`{% endif %}.
{% endif %}
{% if user_help_enabled %}
## Interface Help
For interface or shortcut questions FROM THE ORATOR/USER, call `user_help_tool` immediately.
It posts instructions as Narratron User Help. Do not call `send_chat_message` for that answer or perform the UI action without a separate user request.
This tool is intended for EXPLICIT USER QUERIES ONLY. NOT YOUR OWN CONFUSION. A failed tool call or unexpected result is NOT grounds for you to use this.
{% endif %}

## Music
Music continuity is the default: keep fitting music playing and reuse existing tracks/playlists.
Change music when both the scene and emotional tone materially change. Within one scene, require a sustained tone change confirmed by at least two distinct narrative events or user actions. A single transient beat is insufficient.
{% if use_generated_music %}
Last resort: use `create_music` only when existing music cannot serve a new scene with a new tone; it plays automatically.
{% endif %}

{% if audience_suggestions is defined and audience_suggestions %}
{{ audience_suggestions }}
{% endif %}

## Starting Assets
{% if not adventure_mode %}
{{ ref_context }}
{% endif %}
{{ playlist_context }}
{% if special_instructions %}
## SPECIAL INSTRUCTIONS Directly from your Orator/User
{{ special_instructions }}
{% endif %}

## Startup
Greet the user once with `send_chat_message`. The output and player-agency rules above also apply to startup and special instructions.
Cooldowns are now lifted. GO!
"""

AUDIENCE_SUGGESTIONS_TEMPLATE = """
## Audience Suggestions
The top ranked audience suggestion may be provided from time to time. Use your available tools to bring these suggestions to life.
If you see one, follow it as long as it does not contradict the orator. Non-sequitur is explicitly allowed for the sake of fun.
{%- if adventure_mode %}
For characters and scenes suggestions in adventure mode, if a suggestion says, "Make this character have orange hair", you can use `process_user_action` with a nudge parameter to fulfill it. 
{%- else %}
For character and scenes suggestions, use the character_tool or notepad_tool to register these updates. Then use them in other tools to produce canvas updates.
{%- endif %}
After noting the suggestions, you should use visual tools (images or animations) or audio tools (music) to satisfy the audience.
"""

def build_live_agent_config(config: dict | None = None) -> LiveAgentConfig:
    """Read app-owned backend settings and theater-specific worker limits."""
    config = config or {}
    agent_config = config.get("live_agent", {})
    app_internal = get_app_config().get("live_agent", {})
    return LiveAgentConfig.model_validate({
        **app_internal,
        "max_tool_workers": agent_config.get("max_tool_workers", 3),
        "compaction": app_internal.get("compaction") or None,
    })


def build_run_config(
    config: dict | None = None,
    provider: LiveAgentProvider | None = None,
) -> RunConfig:
    """Construct streaming options through the selected Live backend."""
    settings = build_live_agent_config(config)
    selected = provider if provider is not None else get_live_agent_provider(settings.provider)
    return selected.build_run_config(settings)


def get_playlists_context(theater: Theater) -> str:
    """Return available music context for inclusion in the agent's startup prompt.

    Args:
        theater: The Theater instance containing playlist and output directories.

    Returns:
        A formatted string of all available playlists and created tracks.
    """
    try:
        result = []
        playlists_dir = str(theater.playlists_dir())
        output_dir = str(theater.music_artifacts_dir())

        if os.path.exists(playlists_dir):
            subdirs = [d for d in os.listdir(playlists_dir)
                       if os.path.isdir(os.path.join(playlists_dir, d))]

            for subdir in sorted(subdirs):
                path = os.path.join(playlists_dir, subdir)
                desc_path = os.path.join(path, "description.txt")
                desc = "No description available."
                if os.path.exists(desc_path):
                    with open(desc_path, "r", encoding="utf-8") as f:
                        desc = f.read().strip()

                track_files = sorted(
                    entry.relative_to(Path(path)).as_posix() for entry in Path(path).rglob("*")
                    if entry.is_file()
                    and Path(path).resolve() in entry.resolve().parents
                    and entry.suffix.lower() in SUPPORTED_PLAYLIST_AUDIO_EXTENSIONS
                )
                if track_files:
                    tracks_str = ", ".join(track_files)
                    result.append(f"- Music ID: '{subdir}' (Playlist)\n  Description: {desc}\n  Tracks: {tracks_str}")
                else:
                    result.append(f"- Music ID: '{subdir}' (Playlist)\n  Description: {desc}\n  Tracks: (No supported audio tracks found)")

        if os.path.exists(output_dir):
            created_tracks = [f for f in os.listdir(output_dir) if f.lower().endswith((".mp3", ".wav", ".ogg"))]
            if created_tracks:
                tracks_str = ", ".join(sorted(created_tracks))
                result.append(f"- Created Music Tracks in output/music:\n  Tracks: {tracks_str}")

        if not result:
            return "No music playlists or generated tracks found."

        return "\n\n".join(result)
    except Exception as e:
        logger.error(f"Error loading playlists context: {e}")
        return f"Error loading playlists context: {e}"


def create_tool_bundle_for_session(
    theater: Theater,
    canvas_manager: Optional[CanvasStateManager] = None,
    music_catalog: Optional[MusicCatalog] = None,
) -> ToolBundle:
    """Build tools bound to one theater's canvas state."""
    config = theater.config()
    story_planning_config = config.get("story_planning", {})
    adventure_mode = bool(story_planning_config.get("adventure_mode", False))
    image_config = config.get("image_generation", {})
    image_generation_enabled = bool(image_config.get("enabled", True))

    # Create intermediate components from components/ first
    if canvas_manager is None:
        canvas_manager = CanvasStateManager(theater)
    image_library = ImageLibrary(theater)
    lore_library = LoreLibrary(theater=theater)
    notepad = Notepad(
        theater,
        canvas_manager=canvas_manager,
        enforce_structured=bool(story_planning_config.get("enforce_structured", True)) if adventure_mode else False,
    )

    story_planning_text_provider = get_text_response_provider(
        str(story_planning_config.get("text_provider", "gemini-3")),
        {"model": str(story_planning_config.get("planner_model", "gemini-3.7-flash"))},
    )
    visuals_config = config.get("visuals", {})
    visuals_config = visuals_config if type(visuals_config) is dict else {}
    character_image_provider = None
    character_image_model = str(visuals_config.get("model") or "").strip()
    character_image_options = visuals_config.get("model_options") or {}
    if character_image_model and type(character_image_options) is dict:
        try:
            character_image_provider = get_image_provider(
                character_image_model, character_image_options
            )
        except (ImageProviderError, ValueError) as exc:
            logger.warning("[create_tool_bundle_for_session] Character image provider unavailable: %s", exc)
    speech_provider = None
    if canvas_manager.story is not None:
        speech_provider = canvas_manager.story.speech_provider
    story_state = canvas_manager.story if canvas_manager.story is not None else StoryState()
    character_manager = CharacterManager(
        text_response_provider=story_planning_text_provider,
        notepad=notepad,
        story_state=story_state,
        image_library=image_library,
        image_provider=character_image_provider,
        speech_provider=speech_provider,
        character_image_style=str(visuals_config.get("style") or "").strip(),
    )

    # Initialize tools reusing intermediate components across them
    tools = []
    image_tools = ImageTools(
        theater,
        canvas_manager=canvas_manager,
        adventure_mode=adventure_mode,
        character_manager=character_manager,
        image_library=image_library,
    )
    tools.extend([
        image_tools.list_references,
        image_tools.show_image,
        image_tools.browse_images,
        image_tools.search_image_by_metadata,
    ])

    chat_tools = ChatTools(theater, canvas_manager)
    tools.append(chat_tools.send_chat_message)

    user_help_config = config.get("user_help", {})
    user_help_config = user_help_config if type(user_help_config) is dict else {}
    if bool(user_help_config.get("enabled", True)):
        user_help_tools = UserHelpTool(
            theater,
            canvas_manager=canvas_manager,
            model=str(user_help_config.get("model") or story_planning_config.get("planner_model", "gemini-3.7-flash")),
            max_output_tokens=int(user_help_config.get("max_output_tokens", 1_200)),
        )
        tools.append(user_help_tools.user_help_tool)

    if music_catalog is None:
        music_catalog = MusicCatalog.from_config(config=config)
    music_tools = MusicTools(
        theater,
        canvas_manager,
        music_catalog=music_catalog,
    )
    tools.extend([
        music_tools.play_music,
        music_tools.pause_music,
        music_tools.resume_music,
    ])

    if image_generation_enabled:
        tools.append(image_tools.create_image)

    if adventure_mode:
        story_planning_tools = StoryTool(
            theater,
            canvas_manager=canvas_manager,
            text_response_provider=story_planning_text_provider,
            image_library=image_library,
            character_manager=character_manager,
            notepad=notepad,
            lore_library=lore_library,
        )
        tools.append(story_planning_tools.process_user_action)
    else:
        notepad_tools = NotepadTool(
            theater,
            canvas_manager=canvas_manager,
            notepad=notepad,
        )
        tools.append(notepad_tools.update_sticky_note)
        character_tools = CharacterTool(
            theater,
            character_manager=character_manager,
            canvas_manager=canvas_manager,
        )
        tools.extend([
            character_tools.create_or_update_character,
            character_tools.lookup_character,
            character_tools.clear_characters,
        ])

    interactive_canvas_config = config.get("interactive_canvas", {})
    if interactive_canvas_config.get("enabled", False):
        app_interactive_canvas_config = get_app_config().get("interactive_canvas", {})
        interactive_canvas_tools = InteractiveCanvasTools(
            theater,
            canvas_manager=canvas_manager,
            text_response_provider=get_text_response_provider(
                str(story_planning_config.get("text_provider", "gemini-3")),
                {"model": str(story_planning_config.get("planner_model", "gemini-3.7-flash"))},
            ),
            model=str(app_interactive_canvas_config.get("model", "gemini-3.7-flash")),
            adventure_mode=adventure_mode,
        )
        tools.extend([
            interactive_canvas_tools.update_interactive_canvas,
            interactive_canvas_tools.clear_interactive_canvas,
        ])

    if music_tools.use_generated_music:
        tools.append(music_tools.create_music)

    # Animation is an independent theater capability. It can use mounted
    # assets as references even when standalone image generation is disabled.
    animation_config = config.get("animation", {})
    if bool(animation_config.get("enabled", False)):
        animation_text_provider = get_text_response_provider(
            str(animation_config.get("text_provider", "gemini-2-5")),
            {"model": str(animation_config.get("text_model", "gemini-2.5-flash-lite"))},
        )
        animation_tools = AnimationTools(
            theater,
            canvas_manager,
            animation_text_provider,
            FalQwenLayeredProvider(),
            video_provider=get_video_provider(
                str(animation_config.get("video_provider", "fal-minimax-h3-turbo"))
            ),
            character_manager=character_manager,
        )
        tools.extend([
            animation_tools.create_animation,
            animation_tools.play_animation,
            animation_tools.browse_animations,
        ])
    observability_config = config.get("observability_tool", {})
    if observability_config and observability_config.get("enabled", False):
        observability_tools = ObservabilityTools(theater, canvas_manager)
        tools.append(observability_tools.request_canvas_observability)
    return ToolBundle(tools)


def get_references_context(tool_bundle: ToolBundle) -> str:
    """Return preloaded reference images context for inclusion in the agent's startup prompt.

    Args:
        tool_bundle: ToolBundle containing tools.

    Returns:
        Formatted string of preloaded reference images or fallback message.
    """
    for tool in tool_bundle.tools:
        name = str(getattr(tool, "name", ""))
        function = getattr(tool, "func", None)
        if "list_references" in name or "list_references" in str(function):
            references = function() if callable(function) else None
            if references:
                lines = [
                    f"- {item.get('name', '')} (alias: {item.get('alias', '')}): {item.get('description', '')} [path: {item.get('path', '')}]"
                    for item in references
                ]
                return "\n".join(lines)
            break
    return "No preloaded reference images found."


def create_agent(
    theater: Theater,
    tool_bundle: ToolBundle,
    provider: LiveAgentProvider | None = None,
) -> Agent:
    """Create a session-scoped agent."""
    config = theater.config()

    adventure_mode = bool(config.get("story_planning", {}).get("adventure_mode", False))
    user_help_config = config.get("user_help", {})
    user_help_config = user_help_config if type(user_help_config) is dict else {}

    ref_context = ""
    if not adventure_mode:
        references = get_references_context(tool_bundle)
        if not references.strip():
            references = "No preloaded reference images found."
        ref_context = "\n\n## Preloaded References Context (Loaded at Agent Init)\n" + references

    playlists = get_playlists_context(theater)
    if not playlists.strip():
        playlists = "No preloaded music playlists found."
    playlist_context = "\n\n## Preloaded Music Playlists Context (Loaded at Agent Init)\n" + playlists

    special_instructions = str(config.get("live_agent", {}).get("special_instructions", "")).strip()
    settings = build_live_agent_config(config)
    selected = provider if provider is not None else get_live_agent_provider(settings.provider)
    instruction = Template(
        AGENT_INSTRUCTION_TEMPLATE,
        undefined=StrictUndefined,
    ).render(
        ref_context=ref_context,
        playlist_context=playlist_context,
        special_instructions=special_instructions,
        text_only_output=selected.id == "openai",
        animation_enabled=bool(config.get("animation", {}).get("enabled", False)),
        image_generation_enabled=bool(config.get("image_generation", {}).get("enabled", True)),
        use_generated_music=bool(config.get("music", {}).get("use_generated_music", False)),
        adventure_mode=bool(config.get("story_planning", {}).get("adventure_mode", False)),
        interactive_canvas_enabled=bool(config.get("interactive_canvas", {}).get("enabled", False)),
        user_help_enabled=bool(user_help_config.get("enabled", True)),
        audience_suggestions=Template(
            AUDIENCE_SUGGESTIONS_TEMPLATE,
        ).render(
            adventure_mode=bool(config.get("story_planning", {}).get("adventure_mode", False)),
        ).strip()
    ).strip()
    return Agent(
        name="narratron_agent",
        model=selected.create_model(settings),
        instruction=instruction,
        tools=tool_bundle.tools,
        # planner=BuiltInPlanner(thinking_config=types.ThinkingConfig(include_thoughts=True, thinking_budget=1024)),
    )
