import os
import asyncio
from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService

# Skeleton ADK agent that uses Gemini Live.
# The user opted for Gemini 3.1 Pro (High) for the agent's logic.
narratron_agent = Agent(
    name="narratron_agent",
    model="gemini-3.1-pro",
    tools=[],  # Tools will be built later
)

async def main():
    print("Initializing ADK Agent...")
    session_service = InMemorySessionService()
    # The runner manages the execution context and stream connections.
    runner = Runner(
        app_name="narratron_app",
        agent=narratron_agent, 
        session_service=session_service
    )
    
    # We will hook this runner up to the websocket or live interactions later.
    print("Skeleton ADK agent is ready.")

if __name__ == "__main__":
    asyncio.run(main())
