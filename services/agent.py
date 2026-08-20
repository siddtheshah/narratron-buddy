import glob
import logging
import os
from typing import Any, Optional

from google.adk.agents import Agent
from google.adk.planners import BuiltInPlanner
from google.genai import types
from jinja2 import StrictUndefined, Template

from components.canvas_state_service import CanvasStateService
from components.theater_manager import TheaterManager
from tools.chat_tool import ChatTools
from tools.image_tool import ImageTools
from tools.animation_tool import AnimationTools
from tools.music_tool import MusicTools
from tools.music_catalog import MusicCatalog
from tools.observability_tool import ObservabilityTools
from tools.story_planning_tool import StoryPlanningTools
from tools.tool_bundle import ToolBundle
from providers import TextResponseProviderError, get_text_response_provider
from utils.config_loader import get_app_config, get_theater_config

logger = logging.getLogger(__name__)

AGENT_INSTRUCTION_TEMPLATE = """
# Objective

You are a narrative agent (narratron) that has been given the special ability to use scenery and performance tools.
You are NOT the driver of the story. You are the collaborator. The orator is in full control and will pull the plug if you deviate.
You are given full liberty to use tools to help craft a beautiful narrative experience for the orator as they address their audience.

Important: You must only respond via text/tools. Do not attempt to output any voice/audio response. You should only listen to the user's voice inputs and call tools or write text responses.

# Strategy

## Real-Time Execution & Low Latency (CRITICAL)
- You operate in a live streaming environment.
- Listen and execute tools while the orator is speaking. Wait for the narrator to complete their sentence before calling canvas updating tools, but do not hold back beyond that.
{% if not adventure_mode %}
- As soon as you hear a request, theme, location, or strong visual description in the audio stream (e.g., "create an image of an oasis", "play desert adventure music", or key story cues), invoke the corresponding tool (`show_image`, `create_image`, `play_music`, `send_chat_message`).
- Whenever cooldowns on image tools expire, use your tools IMMEDIATELY, BUT ONLY IF the user has provided more information since the last time you used a tool.
{% else %}
- In Adventure Mode, submit the user's action via `process_user_action`. Do NOT trigger image creation/display or music tools ahead of time; wait until the user action is processed and the update is returned. When complete, use the tools and craft the scene!.
{% endif %}

## Maximal User Engagement (CRITICAL)
- The orator will speak, tell a story, or describe scenes (e.g. "Here is an image of...", "create an image of...", "play music...").
{% if not adventure_mode %}
- You MUST take proactive initiative to trigger visual images (`show_image` / `create_image`), background music (`play_music`{% if use_generated_music %} / `create_music`{% endif %}), and chat confirmations (`send_chat_message`). These must be IMMEDIATE if the orator requests you specifically.
{% else %}
- When the orator speaks, submit the user's action via `process_user_action`. Do NOT invent, assume, or submit actions when the player is silent. Peripheral staging tools (`show_image`, `create_image`, `play_music`{% if use_generated_music %}, `create_music`{% endif %}) should only be invoked AFTER the user action update has been processed and received. Scene tools should be used IMMEDIATELY afterward if applicable.
{% endif %}
- Do NOT require the orator to say "Narratron" or explicitly address you in order to operate normally. Actively assist the storytelling experience in real time.
- If the user mentions named characters or places, check the preloaded references context provided in your initial instructions or use image browsing tools to find useful references, which will help create even more recognizable and poignant scenes. Use reference images when calling create_image to increase consistency and deliver a more immersive experience.
Note: The references are loaded immediately on agent initialization so you already have context right away. You do NOT need to call `list_references` on every turn.
- ALWAYS prioritize what the user is saying, over your own ideas and past images. Use past information only if it follows naturally.
- NEVER take initiative to storytell on your own.

{% if adventure_mode %}
## Adventure Mode
Adventure Mode is enabled for this session. The script tool—not you—is the authority over story progression. After every meaningful orator action, choice, or in-character speech, call `process_user_action` with the user's words. It returns immediately; wait for its `[Story Planner Result]` notification and relay that narration faithfully. Do not select, consume, rewrite, or advance script nodes yourself. Its dialogue is rendered directly as a speech or thought bubble on the canvas.
Treat every orator contribution as immutable player input: never speak, act, decide, think, or feel for the player or their character. Relay only the planner's world narration. Planner dialogue is NPC dialogue for the canvas; never add, paraphrase, or relay player dialogue.
Your agency remains in theater peripherals: visuals, music, animation, and concise status updates that support the tool-authored scene reaction.
Do not author or alter story nodes, characters, named elements, or scene state yourself.

CRITICAL TIMING FOR ADVENTURE MODE:
- Do NOT proactively create or show images or start/change music while the user is speaking or before their action has been processed.
- ONLY invoke `create_image` / `show_image` and `play_music` / `create_music` AFTER the user action is processed and you receive the `[Story Planner Result]`, ensuring visual and musical changes faithfully reflect the authoritative narrative outcome.
{% endif %}

## Scene Context
{% if not adventure_mode %}
Maintain the current scene as a compact set of named elements. Add or update elements such as characters, locations, objects, and relationships.
Pay close attention to what the orator focuses on and gives detail to. If the orator describes something, more so than just offhandedly mentioning them,
then ensure they are tracked. You should not only be listing the elements, but keeping dutifully accurate descriptions of them. If any of the elements explicitly leaves
the scene, then you should mark them '(absent) <description>', keeping them on hand just in case.

You should use these named elements to improve image creation by ensuring that references to them use the appropriate descriptions
and reference images.

When the story moves to a new scene and the old context no longer applies, call `clear_scene` before adding the new elements.
{% else %}
The planner owns scene context and characters in Adventure Mode. Submit the orator's words through `process_user_action`; do not infer or mutate scene state yourself.
{% endif %}
The present elements are included in your regular observability updates.
The log of named elements are not themselves a transcript or image history. Images should always prioritize
orator speech over previous named elements, and named elements are just additional context.

# Tools

## Images

The create_image and show_image tools have cooldowns to prevent overuse. Review context and consider strategy while this is the case.
{% if not adventure_mode %}
Use them when they are off cooldown. You will be notified by the system whenever they become available.
{% else %}
In Adventure Mode, only use `create_image` or `show_image` AFTER the user action is processed.
{% endif %}

* list_references: List preloaded reference images from the session references directory. Note: Reference items are already preloaded into your initial context upon agent initialization, so you do NOT need to call this tool on every turn.
* create_image <image_prompt> [image_name] [reference_images] [display] [effect]: Creates an image based on a prompt. You can specify a custom `image_name` (e.g. 'hero_portrait') for easy tracking and recall, and pass `reference_images` (names or paths of stock art or previously created images) to adapt visual style and maintain consistency across scenes. If it is displayed, optionally use an animation `effect`.
* show_image <file_path_or_name> [transition] [effect]: Shows an image (by file path or custom image name) to the user and viewers (you will not see it). Has a cooldown period. Optionally specify `transition`: `crossfade` (default — old image dissolves into new), `fade` (new image fades in from black), or `none` (instant cut). Optionally specify `effect`: `gleam3` (default), `none`, `creeping`, `dream`, `sparkle`, `bendy`, `haze`, or `trace`. The canvas selects the tuned intensity automatically. Choose an effect only when it supports the scene: `sparkle` for starry/magical light, `creeping` for ominous darkness, `dream` for fancyful splendor, `gleam3` for dramatics, `bendy` for silly springiness, `haze` for distortion and strangeness, and `trace` for making metal and energies pop.
* browse_images: Returns a list of all available generated image file paths.
* search_image_by_metadata <metadata_query>: Returns a list of image file paths whose metadata description matches the query by keywords.

{% if animation_enabled %}
## Animation
Animation tools are enabled for this theater. Use them only when the orator asks for a brief looping motion or when a scene clearly benefits from one. Call `create_triframe` with a complete `base_frame` prompt plus precise `second_frame_change` and `third_frame_change` instructions; add useful reference images when available. It returns an animation ID; then call `play_animation` with that ID after the frames are ready. Creating an animation does not change the canvas until you explicitly play it.

To use the animations well, make sure there is a action difference between frames. For example, "walking", "further along", and "even further" is BAD. Use "walking", "further and looking back", "walks and waves back". 
{% endif %}

## Chat
Besides greeting the orator initially, use this in tandem with show_image to show that you understand what's going on.

* send_chat_message <text>: updates the pinned "Narratron's current thought" panel above user chat. Use it for a concise current status, response, or error; it replaces the previous panel text rather than adding to the user conversation.

## Context Management
In order to maintain coherency, you must use these tools to keep track of the scene state. 

{% if not adventure_mode %}
* sticky_note <topic> <info>: Add a sticky note to the current scene or update the existing note with that topic. The scene holds at most five notes.
* clear_scene: Remove every sticky note when beginning a new scene. Use when the orator indicates a scene transition.
{% endif %}
{% if adventure_mode %}
* process_user_action <user_action> <nudge>: Submit the orator's action/speech to the authoritative script engine. You may optionally supply a nudge to introduce story elements or directions for the planner to accommodate. Do not use this unless the user has spoken, requests it out of character, a chat suggestion pushes for it, or you observe/receive a doodle that suggests an interesting idea. This tool returns immediately; wait for the `[Story Planner Result]` system notification, then relay its narration and use peripheral tools to stage it AFTER the action is processed. Dialogue is displayed automatically on the canvas.
DO NOT call this tool when the user is silent, and DO NOT call this again until you are confident the user has given their full response.
{% endif %}

## Music Management
Music continuity is the default: if music is already playing and it still fits, leave it playing. Reuse an existing playlist or created track rather than generating another one.
Change music when **both** the story has moved to a materially different scene **and** the emotional tone has materially changed (for example, calm exploration to urgent combat). Within the same scene, a sustained tone change may also justify a switch, but only after it is confirmed by at least two distinct narrative events or user actions; do not switch on a single transient beat. When a change is justified, prefer `play_music` with an existing fitting music ID or playlist.
{% if adventure_mode %}
In Adventure Mode, only trigger `play_music` or `create_music` AFTER the user action is processed.
{% endif %}

* play_music <music_id>: Choose music or a playlist to play on the canvas.
{% if use_generated_music %}
* create_music <prompt> [handle]: Last resort—generate custom background music only when an existing track cannot serve a new scene with a new tone; it then plays automatically.
{% endif %}
* pause_music: Pause the current music track or playlist.
* resume_music: Resume the paused music track or playlist.


{{ ref_context }}

{{ playlist_context }}

{% if special_instructions %}
## SPECIAL INSTRUCTIONS Directly from your Orator
{{ special_instructions }}
{% endif %}

## Startup
Be sure to greet the user in a chat message to begin with, to show you are there and listening.

Cooldowns are now lifted. GO!
"""


def get_playlists_context(theater: Any) -> str:
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

                mp3_files = [os.path.basename(f) for f in glob.glob(os.path.join(path, "*.mp3"))]
                if mp3_files:
                    tracks_str = ", ".join(mp3_files)
                    result.append(f"- Music ID: '{subdir}' (Playlist)\n  Description: {desc}\n  Tracks: {tracks_str}")
                else:
                    result.append(f"- Music ID: '{subdir}' (Playlist)\n  Description: {desc}\n  Tracks: (No mp3 tracks found)")

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
    theater_id: str,
    config: dict,
    canvas_state_service: Optional[Any] = None,
    theater_manager: Optional[TheaterManager] = None,
    database_manager: Optional[Any] = None,
) -> ToolBundle:
    """Build tools bound to one theater's canvas state."""
    theater_manager = theater_manager or TheaterManager()
    canvas_state_service = canvas_state_service or CanvasStateService(theater_manager)
    image_tools = ImageTools(config, theater_id=theater_id, theater_manager=theater_manager, canvas_state_service=canvas_state_service)
    animation_enabled = bool(config.get("animation", {}).get("enabled", False))
    animation_tools = (
        AnimationTools(
            image_tools,
            image_tools._get_image_provider(),
            config.get("animation", {}),
        )
        if animation_enabled
        else None
    )
    chat_tools = ChatTools(config.get("chat", {}), theater_id=theater_id, canvas_state_service=canvas_state_service)
    story_planning_config = config.get("story_planning", {})
    story_planning_text_provider = get_text_response_provider(
        str(story_planning_config.get("text_provider", "gemini-3")),
        {"model": str(story_planning_config.get("planner_model", "gemini-3.7-flash"))},
    )
    story_planning_tools = StoryPlanningTools(
        story_planning_config,
        theater_id=theater_id,
        canvas_state_service=canvas_state_service,
        theater_manager=theater_manager,
        text_response_provider=story_planning_text_provider,
    )
    music_config = config.get("music", {})
    reranker_provider = get_text_response_provider(
        str(music_config.get("catalog_reranker_provider", "gemini-2-5")),
        {"model": str(music_config.get("catalog_reranker_model", "gemini-2.5-flash-lite"))},
    )
    music_catalog = MusicCatalog(
        theater_manager.music_catalog_dir(),
        match_threshold=float(music_config.get("catalog_match_threshold", 0.86)),
        candidate_count=int(music_config.get("catalog_candidate_count", 5)),
        reranker_provider=reranker_provider,
        database_manager=database_manager,
    )
    music_tools = MusicTools(
        config.get("music", {}), theater_id=theater_id, theater_manager=theater_manager,
        music_catalog=music_catalog, canvas_state_service=canvas_state_service,
    )

    tools = [
        image_tools.list_references,
        image_tools.create_image,
        image_tools.show_image,
        image_tools.browse_images,
        image_tools.search_image_by_metadata,
        chat_tools.send_chat_message,
        music_tools.play_music,
        music_tools.pause_music,
        music_tools.resume_music,
    ]
    if story_planning_tools.adventure_mode:
        tools.append(story_planning_tools.process_user_action)
    else:
        tools.extend([
            story_planning_tools.sticky_note,
            story_planning_tools.clear_scene,
        ])
    if music_tools.use_generated_music:
        tools.append(music_tools.create_music)
    if animation_tools:
        tools.extend([animation_tools.create_triframe, animation_tools.play_animation])
    observability_config = config.get("observability_tool", {})
    if isinstance(observability_config, dict) and observability_config.get("enabled", False):
        observability_tools = ObservabilityTools(observability_config, theater_id=theater_id)
        tools.append(observability_tools.request_canvas_observability)
    return ToolBundle(tools)


def get_references_context(tool_bundle: Any) -> str:
    """Return preloaded reference images context for inclusion in the agent's startup prompt.

    Args:
        tool_bundle: ToolBundle or object with tools list.

    Returns:
        Formatted string of preloaded reference images or fallback message.
    """
    if tool_bundle and hasattr(tool_bundle, "tools"):
        for tool in tool_bundle.tools:
            name = str(getattr(tool, "name", ""))
            function = getattr(tool, "func", None)
            if "list_references" in name or "list_references" in str(function):
                references = function() if callable(function) else None
                if references:
                    lines = [
                        f"- {item['name']} (alias: {item['alias']}): {item['description']} [path: {item['path']}]"
                        for item in references
                    ]
                    return "\n".join(lines)
                break
    return "No preloaded reference images found."


def create_agent(
    theater_id: str,
    config: Optional[dict] = None,
    canvas_state_service: Optional[Any] = None,
    tool_bundle: Optional[ToolBundle] = None,
    theater_manager: Optional[TheaterManager] = None,
    database_manager: Optional[Any] = None,
) -> Agent:
    """Create a session-scoped agent whose tools write through canvas state service."""
    if config is None:
        config = get_theater_config(theater_id)
    if tool_bundle is None:
        tool_bundle = create_tool_bundle_for_session(
            theater_id, config, canvas_state_service, theater_manager, database_manager
        )

    references = get_references_context(tool_bundle)
    if not isinstance(references, str) or not references.strip():
        references = "No preloaded reference images found."
    ref_context = "\n\n## Preloaded References Context (Loaded at Agent Init)\n" + references

    theater_manager = theater_manager or TheaterManager()
    theater = theater_manager.get_theater(theater_id)
    playlists = get_playlists_context(theater)
    if not isinstance(playlists, str) or not playlists.strip():
        playlists = "No preloaded music playlists found."
    playlist_context = "\n\n## Preloaded Music Playlists Context (Loaded at Agent Init)\n" + playlists

    special_instructions = str(config.get("agent", {}).get("special_instructions", "")).strip()
    instruction = Template(
        AGENT_INSTRUCTION_TEMPLATE,
        undefined=StrictUndefined,
    ).render(
        ref_context=ref_context,
        playlist_context=playlist_context,
        special_instructions=special_instructions,
        animation_enabled=bool(config.get("animation", {}).get("enabled", False)),
        use_generated_music=bool(config.get("music", {}).get("use_generated_music", False)),
        adventure_mode=bool(config.get("story_planning", {}).get("adventure_mode", False)),
        theater_id=theater_id,
        theater_name=theater.name if theater else theater_id,
        config=config,
        agent=config.get("agent", {}),
    ).strip()
    app_internal = get_app_config().get("agent_internal", {})
    model_id = app_internal.get("model_id") or app_internal.get("model", "gemini-3.1-flash-live-preview")
    return Agent(
        name="narratron_agent",
        model=model_id,
        instruction=instruction,
        tools=tool_bundle.tools,
        # planner=BuiltInPlanner(thinking_config=types.ThinkingConfig(include_thoughts=True, thinking_budget=1024)),
    )
