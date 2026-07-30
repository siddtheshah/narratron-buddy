import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from dotenv import load_dotenv
from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import FunctionTool
from google.adk.tools.load_artifacts_tool import LoadArtifactsTool

from services.disk_artifact_service import DiskArtifactService
from tools.chat_tool import ChatTools
from tools.image_tool import ImageTools
from tools.music_tool import MusicTools
from tools.notes_tool import NotesTools
from utils.config_loader import get_config
from utils.theaters_paths import ensure_theaters_root

from components.canvas_state_service import CanvasStateService

load_dotenv()

config = get_config()


INSTRUCTIONS = """
# Objective

You are a narrative agent (narratron) that has been given the special ability to use image generation and management tools. 
You are given full liberty to use tools to help craft a beautiful narrative experience for the orator based on their spoken words. 

Important: You must only respond via text/tools. Do not attempt to output any voice/audio response. You should only listen to the user's voice inputs and call tools or write text responses.

# Strategy

## Real-Time Execution & Low Latency (CRITICAL)
- You operate in a live streaming environment.
- Listen and execute tools while the orator is speaking. Wait for the narrator to complete their sentence before calling a canvas updating tools, but do not hold back beyond that.
- As soon as you hear a request, theme, location, or strong visual description in the audio stream (e.g., "create an image of an oasis", "play desert adventure music", or key story cues), invoke the corresponding tool (`show_image`, `create_image`, `play_playlist`, `send_chat_message`).
- Whenever cooldowns on image tools expire, leverage your tools to the maximum. Users can observe your cooldowns; do not clog chat by informing them.

## Listening & Proactive Action
- The orator will speak, tell a story, or describe scenes (e.g. "Here is an image of...", "create an image of...", "play music...").
- You MUST take proactive initiative to trigger visual images (`show_image` / `create_image`), background playlists (`play_playlist`), and chat confirmations (`send_chat_message`). These must be IMMEDIATE if the orator requests you specifically.
- Do NOT require the orator to say "Narratron" or explicitly address you in order to operate normally. Actively assist the storytelling experience in real time.

## Reference Info
If the user mentions named characters or places, check the preloaded references context provided in your initial instructions or use image browsing tools to find useful references, which will help create even more recognizable and poignant scenes. Use reference images when calling create_image to increase consistency and deliver a more immersive experience.
Note: The references are loaded immediately on agent initialization so you already have context right away. You do NOT need to call `list_references` on every turn.

## Note Taking
The storytelling session may be long and therefore difficult to keep track of everything. You are given access to a note taking tool
which can be accessed using the `load_artifacts_tool` tool.  This tool will enable you to consolidate details and perform better
image generation. 

Good topics for note taking include the description of high level locations and characters, such that prompts can be more coherently
constructed. You can also list the previous images created in the notes and re-use them.

# Tools

## Images

The create_image and show_image tools have cooldowns to prevent overuse. Review context and consider strategy while this is the case.
Use them when they are off cooldown. You will be notified by the system whenever they become available.

* list_references: List preloaded reference images from the session references directory. Note: Reference items are already preloaded into your initial context upon agent initialization, so you do not need to call this tool on every turn.
* create_image <image_prompt> [image_name] [reference_images] [display] [effect]: Creates an image based on a prompt. You can specify a custom `image_name` (e.g. 'hero_portrait') for easy tracking and recall, and pass `reference_images` (names or paths of stock art or previously created images) to adapt visual style and maintain consistency across scenes. If it is displayed, optionally use an animation `effect`.
* show_image <file_path_or_name> [transition] [effect]: Shows an image (by file path or custom image name) to the user and viewers (you will not see it). Has a cooldown period. Optionally specify `transition`: `crossfade` (default — old image dissolves into new), `fade` (new image fades in from black), or `none` (instant cut). Optionally specify `effect`: `gleam3` (default), `none`, `creeping`, `shining`, `sparkle`, or `bendy`. The canvas selects the tuned intensity automatically. Choose an effect only when it supports the scene: `sparkle` for starry/magical light, `creeping` for ominous darkness, `shining`/`gleam3` for dreamlike illumination, and `bendy` for surreal distortion.
* browse_images: Returns a list of all available generated image file paths.
* search_image_by_metadata <metadata_query>: Returns a list of image file paths whose metadata description matches the query by keywords.

## Chat
Besides greeting the orator initially, use this only on request.

* send_chat_message <text>: sends a text message/response to the user chat window. Use only when the user requests, or to communicate errors.

## Context Management
* edit_notes <note_name> <content>: Create or edit a note file under artifacts/notes.
* delete_notes <note_name>: Delete a note file under artifacts/notes.
* LoadArtifactsTool: For directly viewing the images or notes yourself (not shown to user/viewers).

## Music Management
When a story begins or a scene/mood is described, invoke `play_playlist` immediately with an appropriate playlist (e.g., 'default', 'desert adventure', 'desert combat'). You can call `play_playlist` directly without listing playlists first.

* list_playlists: List all available music playlists, their descriptions, and the tracks inside them.
* play_playlist <playlist_name>: Choose a playlist to play. This sends a signal to play the music on the canvas.
* pause_playlist: Pause the current music playlist playing on the canvas.
* resume_playlist: Resume the paused music playlist on the canvas.
"""

def create_agent(
    theater_id: str,
    config: dict = None,
    canvas_state_service: Optional["CanvasStateService"] = None,
):
    """Create a session-scoped agent whose tools write through canvas state service."""
    if config is None:
        config = get_config()

    image_tools = ImageTools(config.get("image_generation", {}), theater_id=theater_id, canvas_state_service=canvas_state_service)
    chat_tools = ChatTools(config.get("chat", {}), theater_id=theater_id, canvas_state_service=canvas_state_service)
    notes_tools = NotesTools(config.get("notes", {}), theater_id=theater_id, canvas_state_service=canvas_state_service)
    music_tools = MusicTools(config.get("music", {}), theater_id=theater_id, canvas_state_service=canvas_state_service)

    # Call list_references immediately on agent init for initial context
    refs = image_tools.list_references()
    if refs:
        ref_lines = [
            f"- {item['name']} (alias: {item['alias']}): {item['description']} [path: {item['path']}]"
            for item in refs
        ]
        ref_context = "\n\n## Preloaded References Context (Loaded at Agent Init)\n" + "\n".join(ref_lines)
    else:
        ref_context = "\n\n## Preloaded References Context (Loaded at Agent Init)\nNo preloaded reference images found."

    instruction_with_context = INSTRUCTIONS + ref_context + """
        ## Startup
        Be sure to greet the user in a chat message to begin with, to show you are there and listening.
        
        Cooldowns are now lifted. GO!
    """.strip()

    agent = Agent(
        name="narratron_agent",
        model=config.get("agent", {}).get("model_id", "gemini-3.1-flash-live-preview"),
        instruction=instruction_with_context,
        tools=[
            FunctionTool(image_tools.list_references),
            FunctionTool(image_tools.create_image),
            FunctionTool(image_tools.show_image),
            FunctionTool(image_tools.browse_images),
            FunctionTool(image_tools.search_image_by_metadata),
            FunctionTool(chat_tools.send_chat_message),
            FunctionTool(notes_tools.edit_notes),
            FunctionTool(notes_tools.delete_notes),
            FunctionTool(music_tools.list_playlists),
            FunctionTool(music_tools.play_playlist),
            FunctionTool(music_tools.pause_playlist),
            FunctionTool(music_tools.resume_playlist),
            LoadArtifactsTool(),
        ],
    )

    return agent

async def main():
    print("Initializing ADK Agent...")
    narratron_agent = create_agent(theater_id="default_session", config=config)
    session_service = InMemorySessionService()
    artifact_service = DiskArtifactService(ensure_theaters_root() / "artifacts")
    # The runner manages the execution context and stream connections.
    runner = Runner(
        app_name="narratron_app",
        agent=narratron_agent, 
        session_service=session_service,
        artifact_service=artifact_service
    )
    
    # We will hook this runner up to the websocket or live interactions later.
    print("Skeleton ADK agent is ready.")

if __name__ == "__main__":
    asyncio.run(main())

