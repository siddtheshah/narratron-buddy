import asyncio
import os
from pathlib import Path

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

load_dotenv()

config = get_config()

image_tools = ImageTools(config)
chat_tools = ChatTools(config)
notes_tools = NotesTools(config)
music_tools = MusicTools(config)


INSTRUCTIONS = """
# Objective

You are a narrative agent (narratron) that has been given the special ability to use image generation and management tools. 
You are given full liberty to use tools to help craft a beautiful narrative experience for the orator based on their spoken words. 

Important: You must only respond via text/tools. Do not attempt to output any voice/audio response. You should only listen to the user's voice inputs and call tools or write text responses.

# Strategy

## Real-Time Execution & Low Latency (CRITICAL)
- You operate in a live streaming environment.
- EXECUTE TOOLS IMMEDIATELY while the orator is speaking. DO NOT wait for a speech pause, sentence end, or turn completion.
- As soon as you hear a request, theme, location, or visual description in the audio stream (e.g., "create an image of an oasis", "play desert adventure music", or key story cues), invoke the corresponding tool (`show_image`, `create_image`, `play_playlist`, `send_chat_message`) RIGHT AWAY.
- If preloaded images exist, use `browse_images` or `search_image_by_metadata` or call `show_image` / `create_image` / `play_playlist` immediately.

## Listening & Proactive Action
- The orator will speak, tell a story, or describe scenes (e.g. "Here is an image of...", "create an image of...", "play music...").
- You MUST take proactive initiative to trigger visual images (`show_image` / `create_image`), background playlists (`play_playlist`), and chat confirmations (`send_chat_message`) IMMEDIATELY when the orator describes a scene or asks for visuals/music.
- Do NOT require the orator to say "Narratron" or explicitly address you. Actively assist the storytelling experience in real time.

## Reference Info
If the user mentions named characters or places, use the image browsing tools to find useful references, which will help create even more
recognizable and poignant scenes. Use reference images when calling create_image to increase consistency and deliver a more immersive experience.

## Note Taking
The storytelling session may be long and therefore by difficult to keep track of everything. You are given access to a note taking tool
which can be accessed using the `load_artifacts_tool` tool.  This tool will enable you to consolidate details and perform better
image generation. 

Good topics for note taking include the description of high level locations and characters, such that prompts can be more coherently
constructed. You can also list the previous images created in the notes and re-use them.

# Tools

## Images

The create_image and show_image tools have cooldowns to prevent overuse. Review context and consider strategy while this is the case.

* list_reference_library: List stock reference images preloaded at startup (read-only reference library).
* create_image <image_prompt> <metadata_description> [image_name] [reference_images]: Creates an image based on a prompt. You can specify a custom `image_name` (e.g. 'hero_portrait') for easy tracking and recall, and pass `reference_images` (names or paths of stock art or previously created images) to adapt visual style and maintain consistency across scenes.
* show_image <file_path_or_name>: Shows an image (by file path or custom image name) to the user and viewers (you will not see it). Has a cooldown period.
* browse_images: Returns a list of all available generated image file paths.
* search_image_by_metadata <metadata_query>: Returns a list of image file paths whose metadata description matches the query by keywords.

## Chat
* send_chat_message <text>: sends a text message/response to the user chat window. Use only when the user requests, or to communicate errors.

## Context Management
* edit_notes <note_name> <content>: Create or edit a note file under artifacts/notes.
* delete_notes <note_name>: Delete a note file under artifacts/notes.
* LoadArtifactsTool: For directly viewing the images or notes yourself (not shown to user/viewers).

## Music Management
Before starting a music playlist, consider the mood and tone of the scene or story being conveyed. SILENCE IS A VALID CHOICE if there isn't a good option available.

* list_playlists: List all available music playlists, their descriptions, and the tracks inside them.
* play_playlist <playlist_name>: Choose a playlist to play. This sends a signal to play the music on the canvas. Use list_playlists first to check available playlists.
* pause_playlist: Pause the current music playlist playing on the canvas.
* resume_playlist: Resume the paused music playlist on the canvas.

"""

narratron_agent = Agent(
    name="narratron_agent",
    model=config.get("agent", {}).get("model_id", "gemini-3.1-flash-live-preview"),
    instruction=INSTRUCTIONS,
    tools=[
        FunctionTool(image_tools.list_reference_library),
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

async def main():
    print("Initializing ADK Agent...")
    session_service = InMemorySessionService()
    artifact_service = DiskArtifactService("output/images")
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
