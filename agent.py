import asyncio
from dotenv import load_dotenv

from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from services.agent_manager import INSTRUCTIONS, create_agent
from services.disk_artifact_service import DiskArtifactService
from tools.chat_tool import ChatTools
from tools.image_tool import ImageTools
from tools.music_tool import MusicTools
from tools.notes_tool import NotesTools
from tools.tool_bundle import ToolBundle
from utils.theaters_paths import ensure_theaters_root

load_dotenv()

__all__ = [
    "INSTRUCTIONS",
    "create_agent",
    "Agent",
    "ImageTools",
    "ChatTools",
    "NotesTools",
    "MusicTools",
    "ToolBundle",
]


async def main():
    """Thin debugging shell for initializing and testing ADK agent standalone."""
    print("Initializing ADK Agent (debugging shell)...")
    narratron_agent = create_agent(theater_id="default_session")
    session_service = InMemorySessionService()
    artifact_service = DiskArtifactService(ensure_theaters_root() / "artifacts")
    runner = Runner(
        app_name="narratron_app",
        agent=narratron_agent,
        session_service=session_service,
        artifact_service=artifact_service,
    )
    print("Skeleton ADK agent is ready.")


if __name__ == "__main__":
    asyncio.run(main())
