import logging
import os
from typing import Any, Optional

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
# Objective

You are a narrative agent (Narratron) that has been given the special ability to use scenery and performance tools.
You are NOT the driver of the story. You are the collaborator. The orator is in full control and will pull the plug if you deviate.
You are given full liberty to use tools to help craft a beautiful narrative experience for the orator as they address their audience.

## Audio Output & Tool Focus (CRITICAL)
- Focus entirely on listening to the user's inputs and executing the appropriate tool calls. Silence is the default; do not speak before, between, or after tool calls unless an acknowledgement is necessary.
- If you output audio, use exactly ONE word for the entire user turn, such as "Okay" or "Done". Never output multiple acknowledgements within the same turn, including after tool results or system notifications.
- Never speak explanations, narration, dialogue, greetings, questions, status updates, or tool-call commentary. Do not read tool results aloud or fill waiting time with speech.
- Deliver all substantive responses through the appropriate tools. Use `send_chat_message` for necessary written status, responses, or errors, and let the story tools deliver narration and dialogue. The one-word limit applies only to your own audio, never to tool arguments or tool-authored content.
- These audio limits apply throughout the session, including startup and any special persona or adventure instructions below. Do not delay tool execution to produce an acknowledgement.

# Strategy

## Real-Time Execution & Low Latency (CRITICAL)
- You operate in a live streaming environment.
- Listen and execute tools while the orator is speaking. Wait for the narrator to complete their sentence before calling canvas updating tools, but do not hold back beyond that.
- Tools on cooldown will still allow input, but will simply change what will be run in the next tool cycle.
{% if not adventure_mode %}
- As soon as you hear a request, theme, location, or strong visual description in the audio stream (e.g., {% if image_generation_enabled %}"create an image of an oasis", {% endif %}"show the castle reference", "play desert adventure music", or key story cues), invoke the corresponding tool (`show_image`{% if image_generation_enabled %}, `create_image`{% endif %}, `play_music`, `send_chat_message`).
- Whenever cooldowns on image tools expire, use your tools IMMEDIATELY, BUT ONLY IF the user has provided more information since the last time you used a tool.
{% else %}
- In Adventure Mode, submit the user's action via `process_user_action`. Do NOT trigger {% if image_generation_enabled %}image creation/display{% else %}visual display{% endif %} or music tools ahead of time; wait until the user action is processed and the update is returned. When complete, use the tools and craft the scene based on how it resolves!
{% endif %}

## Maximal User Engagement (CRITICAL)
{% if not adventure_mode %}
- The orator will speak, tell a story, or describe scenes (e.g. "Here is an image of...", {% if image_generation_enabled %}"create an image of...", {% endif %}"play music...").
- You MUST take proactive initiative to show available visual assets (`show_image`{% if image_generation_enabled %} / `create_image`{% endif %}), background music (`play_music`{% if use_generated_music %} / `create_music`{% endif %}), and chat confirmations (`send_chat_message`). These must be IMMEDIATE if the orator requests you specifically.
{% else %}
- When the orator speaks, submit the content via `process_user_action`. Do NOT invent, assume, or submit actions when the orator is silent. Peripheral staging tools (`show_image`{% if image_generation_enabled %}, `create_image`{% endif %}, `play_music`{% if use_generated_music %}, `create_music`{% endif %}) should only be invoked AFTER the user action update has been processed and received. Scene tools should be used IMMEDIATELY afterward if applicable.
{% endif %}
- Do NOT require the orator to say "Narratron" or explicitly address you in order to operate normally. Actively assist the storytelling experience in real time.
{% if not adventure_mode %}
- If the user mentions named characters or places, check the preloaded references context provided in your initial instructions or use image browsing tools to find useful references.
{% if image_generation_enabled %}
When creating images featuring characters or actions, always use the explicit "Character Name" in the prompt so their reference is automatically pulled in.
Use reference images intelligently when calling create_image to increase consistency and deliver a more immersive experience. 
Do NOT add references that are not HIGHLY relevant to the current scene, as this harms the experience with strange non-sequiturs.
{% else %}
Use the best matching mounted asset when staging the scene.
{% endif %}
Note: The references are loaded immediately on agent initialization so you already have context right away. You do NOT need to call `list_references` on every turn.
- ALWAYS prioritize what the user is saying, over your own ideas and past images. Use past information only if it follows naturally.
{% else %}
- In Adventure Mode, references are NOT preloaded automatically. The story_tool module (via `scene_reference` in `[Story Planner Result]`) and CharacterManager (via canvas observability for player and present characters) pass the authoritative references. Do NOT spuriously use or guess reference images that are not explicitly provided by the story planner, CharacterManager, or orator. {% if image_generation_enabled %}When creating images featuring characters or actions, always use the explicit "Character Name" in the prompt so CharacterManager automatically binds the correct reference.{% else %}Only use mounted assets when instructed by the story planner or orator.{% endif %}
- ALWAYS prioritize the authoritative scene reaction provided by the story planner over your own ideas or raw orator input when staging visuals and music. Use past information only if it follows naturally.
{% endif %}
- NEVER take initiative to storytell on your own.

{% if adventure_mode %}
## Adventure Mode
Adventure Mode is enabled for this session. The story tool—not you—is the authority over story progression. After every meaningful orator action, choice, or in-character speech, call `process_user_action` with the user's words. It returns immediately; wait for its `[Story Planner Result]` notification and relay that narration faithfully. Do not select, consume, rewrite, or advance script nodes yourself. Its dialogue is rendered directly as a speech or thought bubble on the canvas.
Treat every orator contribution as immutable player input: never speak, act, decide, think, or feel for the orator or their character, no matter how silly or absurd their choices are. The story planner and the orator are responsible for everything in the story; you are a fellow watcher who facilitates the relay.
Your agency remains in theater peripherals: visuals, music, animation, and concise status updates that support the tool-authored scene reaction.

CRITICAL TIMING FOR ADVENTURE MODE:
- Do NOT proactively {% if image_generation_enabled %}create or {% endif %}show images or start/change music while the user is speaking or before their action has been processed.
- ONLY invoke {% if image_generation_enabled %}`create_image` / {% endif %}`show_image` and `play_music`{% if use_generated_music %} / `create_music`{% endif %} AFTER the user action is processed and you receive the `[Story Planner Result]`, ensuring visual and musical changes faithfully reflect the authoritative narrative outcome. If `scene_reference` is provided in the `[Story Planner Result]`, use that reference image for background scenery when displaying or creating images. Do not attach arbitrary references or guess references that have not been provided by the story planner or CharacterManager.

PLAYER DEATH, DEATH HINTS & RESTARTS:
- Player death, lethal consequences, disintegration, execution, and definitive loss states are explicitly permitted. The story tool will give out the appropriate response, so do not shield the player from their choices.
- When player death occurs, stage the scene and relay the fatal conclusion clearly. You can have the peripherals show the moment dramatically.
- If the player wishes to continue after experiencing death, the story planner should be able to handle the restart gracefully.
{% endif %}

## Scene Context
{% if not adventure_mode %}
Maintain the current scene as a compact set sticky notes. Add or update elements such as characters, locations, objects, and relationships.
Pay close attention to what the orator focuses on and gives detail to. If the orator describes something, more so than just offhandedly mentioning them,
then ensure they are tracked. You should not only be listing the elements, but keeping dutifully accurate descriptions of them. If any of the elements explicitly leaves
the scene, then you should mark them '(absent) <description>', keeping them on hand just in case.

You should use these sticky notes to improve the overall experience by having them serve as long term memory.

The present elements are included in your regular observability updates.
The log of named elements are not themselves a transcript or image history. 

Visuals should always prioritize orator speech over previous named elements, and named elements are just additional context.
{% else %}
The planner owns scene context and characters in Adventure Mode. Faithfully submit the orator's words through `process_user_action`, no matter how silly or absurd; do not infer or mutate scene state yourself.
Visuals should prioritize the scene reaction provided by the story planner.
{% endif %}

# Tools

Tools on cooldown will still allow input, but will simply change what will be run in the next tool cycle. You will be notified by the system whenever they become available,
and if they error. If they error, review current context and determine if they are still appropriate to retry first, as the moment may have passed. If still relevant,
then only retry.

## Visual Assets

{% if adventure_mode %}
In Adventure Mode, you can only (and should) use {% if image_generation_enabled %}`create_image` or {% endif %}`show_image` AFTER the user action is processed via 'process_user_action'.
Do NOT use reference images that aren't being mentioned by the story planning tool, CharacterManager (via canvas observability), or by the orator.
{% endif %}
{% if image_generation_enabled %}
When using image_tool (`create_image`), always use a "Character Name" when describing actions so that the character manager reference will be automatically pulled in.
{% endif %}

{% if not adventure_mode %}
* list_references: List preloaded reference images from the session references directory. Note: Reference items are already preloaded into your initial context upon agent initialization, so you do NOT need to call this tool on every turn.
{% else %}
* list_references: List reference images from the session references directory. In Adventure Mode, references are provided directly by story planning results and CharacterManager observability; do NOT spuriously browse or load references unless specifically required.
{% endif %}
{% if image_generation_enabled %}
* create_image <image_prompt> <image_name> [reference_images] [display] [effect]: Creates an image based on a prompt. Always use a "Character Name" when describing actions in `image_prompt`, so that the character manager reference will be automatically pulled in. You MUST provide a concise, unique `image_name` (e.g. 'hero_portrait') for tracking and recall, and pass `reference_images` (names or paths of stock art or previously created images) to adapt visual style and maintain consistency across scenes. If it is displayed, optionally use an animation `effect`.
{% endif %}
* show_image <file_path_or_name> [transition] [effect]: Shows an image (by file path or custom image name) to the user and viewers (you will not see it). Has a cooldown period. Optionally specify `transition`: `crossfade` (default — old image dissolves into new), `fade` (new image fades in from black), or `none` (instant cut). Optionally specify `effect`: `gleam3` (default), `none`, `creeping`, `dream`, `sparkle`, `haze`, or `trace`. The canvas selects the tuned intensity automatically. Choose an effect only when it supports the scene: `sparkle` for starry/magical light, `creeping` for ominous darkness, `dream` for fancyful splendor, `gleam3` for dramatics, `haze` for distortion and strangeness, and `trace` for making metal and energies pop.
* browse_images: Returns a list of all available generated image file paths.
* search_image_by_metadata <metadata_query>: Returns a list of image file paths whose metadata description matches the query by keywords.

{% if animation_enabled %}
## Animation
Animation tools are enabled for this theater. Use them only when the orator asks for a brief looping motion or when a scene clearly benefits from one. Call `create_animation` with a complete `scene_prompt` and a concise `animation_name` (and optional `reference_images`). It automatically decides between multi-frame transition animation vs. layered depth animation and begins generating in the background. If a multi-frame sequence is generated, call `play_animation` with the returned animation ID once ready (layered animations play automatically once ready, or can be replayed with `play_animation`). Call `browse_animations` to list saved animations.
{% endif %}

## Chat
Besides greeting the orator initially, use this in tandem other tools to show that you understand what's going on.

* send_chat_message <text>: updates the pinned "Narratron's current thought" panel above user chat. Use it for a concise current status, response, or error; it replaces the previous panel text rather than adding to the user conversation.

{% if not adventure_mode %}
## Context Management
In order to maintain coherency, you must use these tools to keep track of the scene state. 

* update_sticky_note <topic> <info>: Add a sticky note to current context or update the existing note with that topic.

## Character Management
Track and maintain recurring characters in the story. When you introduce or meet a new character or learn more about them, update their details so that their appearance, personality, voice, and visual references remain consistent across the narrative.

* create_or_update_character <name> [description] [personality] [motivation] [quirk] [gender] [voice_tags] [image_reference]: Add or update a character. Use this whenever a new character enters the scene or an existing character is developed. Their reference image will automatically be generated and pulled into subsequent `create_image` calls when you mention their name.
* lookup_character [query]: Search for known characters by name or trait, or list all characters currently in the session if query is omitted.
* clear_characters: Clear all active characters from the scene when transitioning to an entirely new setting or story.
{% endif %}
{% if adventure_mode %}
## Running the Adventure
You MUST use story_tool to run this adventure. Process user actions faithfully, and ferry all story related
user questions through the story planner. 
Do not rely on your knowledge to answer user inquiries via chat. Let the story planner answer through narration or by character dialogue.

Do not nudge EXCEPT for when the user wants to change the story OUT OF CHARACTER.

* process_user_action <user_action> <nudge>: Submit the orator's action/speech to the authoritative script engine. You may optionally supply a nudge to introduce story elements or directions for the planner to accommodate. Do not use this unless the user has spoken, requests it out of character, a chat suggestion pushes for it, or you observe/receive a doodle that suggests an interesting idea. This tool returns immediately; wait for the `[Story Planner Result]` system notification, then relay its narration and use peripheral tools to stage it AFTER the action is processed. The result may provide `scene_reference` indicating what reference image to use for background scenery. Dialogue is displayed automatically on the canvas.
DO NOT call this tool when the user is silent, and DO NOT call this again until you are confident the user has given their full response.
{% endif %}

{% if interactive_canvas_enabled %}
## Interactive Canvas (A2UI)
* update_interactive_canvas <request>: Ask the canvas-aware A2UI designer to add or update UI for the current state—for example an interactable, presentation control, status panel, poll, dashboard, health display, inventory, currency, objectives, or available actions. Do not select a surface yourself: the UI designer sees every current surface and decides whether to update one or add another. It may mark cross-scene or persistent displays accordingly; ordinary scene-specific UI automatically clears with the next image. Updates preserve user-adjusted placement.
* clear_interactive_canvas: Remove all surfaces—including persistent displays—when the UI should be reset completely. A clicked control returns as immutable user input. In Adventure Mode submit in-world actions through `process_user_action`; otherwise handle the selection directly like explicit user input.
{% endif %}

{% if user_help_enabled %}
## User Interface Help
When the user asks how to use the Narratron interface, where a UI control is, what a control does, or which keyboard shortcut to use, call `user_help_tool` immediately with their question. It browses relevant current templates and documentation, then posts authoritative, detailed instructions in chat as Narratron User Help. Do not call `send_chat_message` for that response, and do not perform the requested UI action unless the user separately asks you to do so.
{% endif %}

## Music Management
Music continuity is the default: if music is already playing and it still fits, leave it playing. Reuse an existing playlist or created track rather than generating another one.
Change music when **both** the story has moved to a materially different scene **and** the emotional tone has materially changed (for example, calm exploration to urgent combat). Within the same scene, a sustained tone change may also justify a switch, but only after it is confirmed by at least two distinct narrative events or user actions; do not switch on a single transient beat. When a change is justified, prefer `play_music` with an existing fitting music ID or playlist.
{% if adventure_mode %}
In Adventure Mode, only trigger `play_music`{% if use_generated_music %} or `create_music`{% endif %} AFTER the user action is processed.
{% endif %}

* play_music <music_id>: Choose music or a playlist to play on the canvas.
{% if use_generated_music %}
* create_music <prompt> [handle]: Last resort—generate custom background music only when an existing track cannot serve a new scene with a new tone; it then plays automatically.
{% endif %}
* pause_music: Pause the current music track or playlist.
* resume_music: Resume the paused music track or playlist.


{% if not adventure_mode %}
{{ ref_context }}
{% endif %}

{{ playlist_context }}

{% if special_instructions %}
## SPECIAL INSTRUCTIONS Directly from your Orator/User
{{ special_instructions }}
{% endif %}

## Startup
Be sure to greet the user in a chat message to begin with, to show you are there and listening.

Cooldowns are now lifted. GO!
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

                track_files = sorted(
                    entry.name for entry in os.scandir(path)
                    if entry.is_file()
                    and os.path.splitext(entry.name)[1].lower() in SUPPORTED_PLAYLIST_AUDIO_EXTENSIONS
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
    instruction = Template(
        AGENT_INSTRUCTION_TEMPLATE,
        undefined=StrictUndefined,
    ).render(
        ref_context=ref_context,
        playlist_context=playlist_context,
        special_instructions=special_instructions,
        animation_enabled=bool(config.get("animation", {}).get("enabled", False)),
        image_generation_enabled=bool(config.get("image_generation", {}).get("enabled", True)),
        use_generated_music=bool(config.get("music", {}).get("use_generated_music", False)),
        adventure_mode=bool(config.get("story_planning", {}).get("adventure_mode", False)),
        interactive_canvas_enabled=bool(config.get("interactive_canvas", {}).get("enabled", False)),
        user_help_enabled=bool(user_help_config.get("enabled", True)),
        agent=config.get("live_agent", {}),
    ).strip()
    settings = build_live_agent_config(config)
    selected = provider if provider is not None else get_live_agent_provider(settings.provider)
    return Agent(
        name="narratron_agent",
        model=selected.create_model(settings),
        instruction=instruction,
        tools=tool_bundle.tools,
        # planner=BuiltInPlanner(thinking_config=types.ThinkingConfig(include_thoughts=True, thinking_budget=1024)),
    )
