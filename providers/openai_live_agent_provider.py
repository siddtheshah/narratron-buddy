"""OpenAI Realtime backend using ADK for Narratron's existing tool execution."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
import os

from google.adk.agents.run_config import RunConfig, StreamingMode, ToolThreadPoolConfig
from google.adk.events import Event
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.sessions.base_session_service import GetSessionConfig
from google.genai import types
from openai import AsyncOpenAI
from openai.types.realtime.realtime_function_tool_param import RealtimeFunctionToolParam
from openai.types.realtime.realtime_session_create_request_param import (
    RealtimeSessionCreateRequestParam,
)
from pydantic import Field, JsonValue, TypeAdapter

from providers.live_agent_provider import (
    LiveAgentConfig,
    LiveAgentProvider,
    LiveAgentProviderError,
    LiveAgentRunRequest,
    OpenAIRealtimeConfig,
)
from providers.openai_realtime_connection import OpenAIRealtimeConnection

DEFAULT_OPENAI_LIVE_MODEL = "gpt-realtime-2.1"
_JSON_SCHEMA = TypeAdapter(dict[str, JsonValue])
_INSTRUCTION = TypeAdapter(str)
_FOLLOW_THROUGH_INSTRUCTION = """
Be an active stage director within the agency allowed by the instructions above.
After completed user input, carry out the relevant available tools without asking
the user to prompt you again. When a [Story Planner Result] arrives, immediately
stage the authoritative scene using appropriate visuals, music, and animation;
do not wait for another player action. When an animation becomes ready, display
it if it still fits the current scene. Follow through on relevant pending work
after tool results, and run independent staging tools together when appropriate.
Respect existing timing, cooldown, and music-continuity rules. Never invent player
actions or advance the adventure independently. Once staging is complete, wait
for new input or an actionable notification; do not repeatedly poll tools.
""".strip()


def _convert_schema(schema: types.Schema) -> dict[str, JsonValue]:
    """Convert Google's uppercase schema types and nullable flag to JSON Schema."""
    result: dict[str, JsonValue] = {}
    if schema.type is not None:
        schema_type = schema.type.value.lower()
        result["type"] = [schema_type, "null"] if schema.nullable else schema_type
    if schema.description is not None:
        result["description"] = schema.description
    if schema.enum is not None:
        result["enum"] = schema.enum
    if schema.required is not None:
        result["required"] = schema.required
    if schema.properties is not None:
        result["properties"] = {
            name: _convert_schema(value) for name, value in schema.properties.items()
        }
    if schema.items is not None:
        result["items"] = _convert_schema(schema.items)
    if schema.any_of is not None:
        result["anyOf"] = [_convert_schema(value) for value in schema.any_of]
    for name, value in (
        ("minimum", schema.minimum),
        ("maximum", schema.maximum),
        ("minItems", schema.min_items),
        ("maxItems", schema.max_items),
        ("minLength", schema.min_length),
        ("maxLength", schema.max_length),
        ("pattern", schema.pattern),
        ("format", schema.format),
    ):
        if value is not None:
            result[name] = value
    return result


def build_openai_session_config(
    request: LlmRequest,
    model: str,
    config: OpenAIRealtimeConfig,
) -> RealtimeSessionCreateRequestParam:
    """Map ADK instructions and function declarations to the GA session schema."""
    tools: list[RealtimeFunctionToolParam] = []
    for tool in request.config.tools or []:
        # ADK assembles types.Tool objects; validate the schema contract explicitly.
        declaration_tool = types.Tool.model_validate(tool)
        for declaration in declaration_tool.function_declarations or []:
            if not declaration.name:
                raise LiveAgentProviderError("OpenAI tools require a name")
            parameters: dict[str, JsonValue] = {"type": "object", "properties": {}}
            if declaration.parameters_json_schema is not None:
                parameters = _JSON_SCHEMA.validate_python(
                    declaration.parameters_json_schema
                )
            elif declaration.parameters is not None:
                parameters = _convert_schema(declaration.parameters)
            tools.append(
                {
                    "type": "function",
                    "name": declaration.name,
                    "description": declaration.description or "",
                    "parameters": parameters,
                }
            )
    session: RealtimeSessionCreateRequestParam = {
        "type": "realtime",
        "model": model,
        "instructions": _INSTRUCTION.validate_python(
            request.config.system_instruction or ""
        )
        + "\n\n"
        + _FOLLOW_THROUGH_INSTRUCTION,
        "output_modalities": ["text"],
        "max_output_tokens": config.max_output_tokens,
        "audio": {
            "input": {
                "format": {"type": "audio/pcm", "rate": 24000},
                "turn_detection": None,
            }
        },
        "tools": tools,
        "tool_choice": "auto",
        "truncation": {
            "type": "retention_ratio",
            "retention_ratio": config.retention_ratio,
            "token_limits": {"post_instructions": config.post_instructions_token_limit},
        },
    }
    terminal_tools = terminal_tool_names(request)
    if terminal_tools:
        session["instructions"] += (
            "\n\nRequest all independent staging actions together when their "
            "arguments are already known. Successful results from these tools "
            "may not trigger another response: "
            + ", ".join(sorted(terminal_tools))
            + ". Include all already-planned actions in that response rather "
            "than relying on an acknowledgement turn. Results needing further "
            "decisions and errors will still allow continuation."
        )
    if config.input_transcription_model:
        session["audio"]["input"]["transcription"] = {
            "model": config.input_transcription_model
        }
    return session


def terminal_tool_names(request: LlmRequest) -> set[str]:
    """Read the tool-owned policy without sending custom fields to OpenAI."""
    return {
        name for name, tool in request.tools_dict.items()
        if tool.custom_metadata is not None and tool.custom_metadata.get("terminal") is True
    }


class OpenAIRealtimeModel(BaseLlm):
    """ADK model adapter; credentials and connections are created only on use."""

    realtime_config: OpenAIRealtimeConfig = Field(default_factory=OpenAIRealtimeConfig)

    async def generate_content_async(
        self,
        llm_request: LlmRequest,
        stream: bool = False,
    ) -> AsyncGenerator[LlmResponse, None]:
        raise LiveAgentProviderError("OpenAIRealtimeModel requires ADK BIDI streaming")
        yield LlmResponse()  # Keep the abstract method's async-generator contract.

    @asynccontextmanager
    async def connect(
        self, llm_request: LlmRequest
    ) -> AsyncGenerator[OpenAIRealtimeConnection, None]:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise LiveAgentProviderError(
                "Set OPENAI_API_KEY to use the OpenAI Live provider"
            )
        session = build_openai_session_config(
            llm_request, self.model, self.realtime_config
        )
        async with AsyncOpenAI(api_key=api_key) as client:
            # No transparent reconnect: a fresh socket loses server conversation state.
            async with client.realtime.connect(model=self.model) as socket:
                connection = OpenAIRealtimeConnection(
                    socket, self.model, self.realtime_config,
                    terminal_tools=terminal_tool_names(llm_request),
                )
                try:
                    await socket.session.update(session=session)
                    await connection.wait_for_setup()
                    yield connection
                finally:
                    await connection.close()


class OpenAILiveAgentProvider(LiveAgentProvider):
    id = "openai"
    display_name = "OpenAI Realtime"
    background_content_is_partial = True
    requires_tool_reminders = False
    tool_results_bypass_input_window = True

    def create_model(self, config: LiveAgentConfig) -> OpenAIRealtimeModel:
        return OpenAIRealtimeModel(
            model=config.model_id or config.legacy_model or DEFAULT_OPENAI_LIVE_MODEL,
            realtime_config=config.openai,
        )

    def build_run_config(self, config: LiveAgentConfig) -> RunConfig:
        return RunConfig(
            streaming_mode=StreamingMode.BIDI,
            response_modalities=["TEXT"],
            input_audio_transcription=None,
            output_audio_transcription=None,
            tool_thread_pool_config=ToolThreadPoolConfig(
                max_workers=config.max_tool_workers
            ),
            get_session_config=GetSessionConfig(num_recent_events=0),
        )

    def run_live(self, request: LiveAgentRunRequest) -> AsyncGenerator[Event, None]:
        return request.runner.run_live(
            user_id=request.user_id,
            session_id=request.session_id,
            live_request_queue=request.input_queue,
            run_config=request.run_config,
        )
