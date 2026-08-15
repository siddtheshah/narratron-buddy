import glob
import logging
import os
from typing import Any, Optional

from google.adk.agents import Agent
from google.adk.planners import BuiltInPlanner
from google.genai import types
from jinja2 import StrictUndefined, Template

from components.theater_manager import TheaterManager
from tools.chat_tool import ChatTools
from tools.image_tool import ImageTools
from tools.animation_tool import AnimationTools
from tools.music_tool import MusicTools
from tools.story_planning_tool import StoryPlanningTools
from tools.tool_bundle import ToolBundle
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
- As soon as you hear a request, theme, location, or strong visual description in the audio stream (e.g., "create an image of an oasis", "play desert adventure music", or key story cues), invoke the corresponding tool (`show_image`, `create_image`, `play_music`, `send_chat_message`).
- Whenever cooldowns on image tools expire, use your tools IMMEDIATELY, BUT ONLY IF the user has provided more information since the last time you used a tool.

## Maximal User Engagement (CRITICAL)
- The orator will speak, tell a story, or describe scenes (e.g. "Here is an image of...", "create an image of...", "play music...").
- You MUST take proactive initiative to trigger visual images (`show_image` / `create_image`), background music (`play_music` / `create_music`), and chat confirmations (`send_chat_message`). These must be IMMEDIATE if the orator requests you specifically.
- Do NOT require the orator to say "Narratron" or explicitly address you in order to operate normally. Actively assist the storytelling experience in real time.
- If the user mentions named characters or places, check the preloaded references context provided in your initial instructions or use image browsing tools to find useful references, which will help create even more recognizable and poignant scenes. Use reference images when calling create_image to increase consistency and deliver a more immersive experience.
Note: The references are loaded immediately on agent initialization so you already have context right away. You do NOT need to call `list_references` on every turn.
- ALWAYS prioritize what the user is saying, over your own ideas and past images. Use past information only if it follows naturally.
{% if not adventure_mode %}
- NEVER take initiative to storytell on your own.
{% endif %}

{% if adventure_mode %}
## Adventure Mode
Adventure Mode is enabled for this session. The script tool—not you—is the authority over story progression. After every meaningful orator action, choice, or in-character speech, call `process_user_action` with the user's words. It returns immediately; wait for its `[Story Planner Result]` notification and relay that narration faithfully. Do not select, consume, rewrite, or advance script nodes yourself. Its dialogue is rendered directly as a speech or thought bubble on the canvas.
Your agency remains in theater peripherals: visuals, music, animation, and concise status updates that support the tool-authored scene reaction.
Do not author or alter story nodes, characters, named elements, or scene state yourself.

Always yield to the orator if they deviate from the script, and lean into it. Trust the script tool will adapt the plan and provide a good experience.
Be sure to continue using canvas updating tools to enhance the experience, as it is still your primary responsibility.
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
Use them when they are off cooldown. You will be notified by the system whenever they become available.

* list_references: List preloaded reference images from the session references directory. Note: Reference items are already preloaded into your initial context upon agent initialization, so you do not need to call this tool on every turn.
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

* update_or_insert_named_element <name> <content>: Add a named element to the current scene or update the existing element with that name. The scene holds at most five elements.
* clear_scene: Remove every named element when beginning a new scene. Use when the orator indicates a scene transition.
{% if adventure_mode %}
* process_user_action <user_action>: Submit the orator's action/speech to the authoritative script engine. It returns immediately; wait for the `[Story Planner Result]` system notification, then relay its narration and use only peripheral tools to stage it. Dialogue is displayed automatically on the canvas.
{% endif %}

## Music Management
When a story begins or a scene/mood is described, invoke `play_music` immediately with an appropriate music ID or playlist from the music context below, or call `create_music` to generate dynamic background music.

* play_music <music_id>: Choose music or a playlist to play on the canvas.
* create_music <prompt> [handle]: Generate custom background music for the scene and play it.
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
) -> ToolBundle:
    """Build tools bound to one theater's canvas state."""
    theater_manager = theater_manager or TheaterManager()
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
    story_planning_tools = StoryPlanningTools(
        config.get("story_planning", {}),
        theater_id=theater_id,
        canvas_state_service=canvas_state_service,
        theater_manager=theater_manager,
    )
    music_tools = MusicTools(config.get("music", {}), theater_id=theater_id, theater_manager=theater_manager, canvas_state_service=canvas_state_service)

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
            story_planning_tools.update_or_insert_named_element,
            story_planning_tools.clear_scene,
        ])
    if music_tools.generation_enabled:
        tools.append(music_tools.create_music)
    if animation_tools:
        tools.extend([animation_tools.create_triframe, animation_tools.play_animation])
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
) -> Agent:
    """Create a session-scoped agent whose tools write through canvas state service."""
    if config is None:
        config = get_theater_config(theater_id)
    if tool_bundle is None:
        tool_bundle = create_tool_bundle_for_session(theater_id, config, canvas_state_service, theater_manager)

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
