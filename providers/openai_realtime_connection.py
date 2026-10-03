"""Translate ADK Live input and events to the OpenAI Realtime wire protocol."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncGenerator
from functools import singledispatchmethod
import json
import logging

import av
from google.adk.models.base_llm_connection import BaseLlmConnection
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from openai.resources.realtime.realtime import AsyncRealtimeConnection
from openai.types.realtime.realtime_conversation_item_user_message_param import Content
from pydantic import BaseModel, Field, JsonValue, TypeAdapter

from providers.live_agent_provider import LiveAgentProviderError, OpenAIRealtimeConfig

logger = logging.getLogger(__name__)
_ARGUMENTS = TypeAdapter(dict[str, JsonValue])


class _TextPart(BaseModel):
    type: str
    text: str = ""


class _OutputItem(BaseModel):
    type: str
    call_id: str = ""
    name: str = ""
    arguments: str = "{}"
    content: list[_TextPart] = Field(default_factory=list)


class _Error(BaseModel):
    code: str = "unknown"
    message: str = "OpenAI Realtime request failed"


class _StatusDetails(BaseModel):
    error: _Error | None = None
    reason: str | None = None


class _ModalTokenDetails(BaseModel):
    audio_tokens: int = 0
    text_tokens: int = 0
    image_tokens: int = 0


class _TokenDetails(_ModalTokenDetails):
    cached_tokens: int = 0
    cached_tokens_details: _ModalTokenDetails = Field(
        default_factory=_ModalTokenDetails
    )


class _Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    input_token_details: _TokenDetails = Field(default_factory=_TokenDetails)
    output_token_details: _TokenDetails = Field(default_factory=_TokenDetails)


class _Response(BaseModel):
    id: str
    status: str
    output: list[_OutputItem] = Field(default_factory=list)
    usage: _Usage | None = None
    status_details: _StatusDetails | None = None


class _Session(BaseModel):
    id: str = ""
    output_modalities: list[str] = Field(default_factory=list)


class _ServerEvent(BaseModel):
    """Only the wire fields the adapter consumes, with explicit defaults."""

    type: str
    session: _Session | None = None
    response: _Response | None = None
    error: _Error | None = None
    delta: str = ""
    transcript: str = ""


class OpenAIRealtimeConnection(BaseLlmConnection):
    """One text-output session with manual turns and serialized response creation."""

    def __init__(
        self,
        socket: AsyncRealtimeConnection,
        model: str,
        config: OpenAIRealtimeConfig,
    ) -> None:
        self._socket = socket
        self._model = model
        self._config = config
        self._send_lock = asyncio.Lock()
        self._closed = False
        self._speaking = False
        self._audio_bytes = 0
        self._resampler = av.AudioResampler(format="s16", layout="mono", rate=24000)
        self._response_active = False
        self._response_pending = False
        self._turns_remaining = 0
        self._pending_calls: set[str] = set()
        self._completed_responses: set[str] = set()
        self._session_id: str | None = None

    async def wait_for_setup(self) -> None:
        """Wait for configuration acknowledgement before accepting input."""
        await asyncio.wait_for(self._wait_for_setup(), timeout=15)

    async def _wait_for_setup(self) -> None:
        while True:
            event = _ServerEvent.model_validate_json(await self._socket.recv_bytes())
            if event.type == "error":
                self._raise_error(event.error)
            if event.session is not None:
                self._session_id = event.session.id
            if event.type == "session.updated":
                if event.session is None or event.session.output_modalities != ["text"]:
                    raise LiveAgentProviderError(
                        "OpenAI did not confirm text-only output"
                    )
                return

    @staticmethod
    def _raise_error(error: _Error | None) -> None:
        detail = (
            f"{error.code}: {error.message}" if error is not None else "unknown error"
        )
        raise LiveAgentProviderError(f"OpenAI Realtime: {detail}")

    async def _start_response_if_ready(self) -> None:
        """Called under the send lock; never overlap paid responses."""
        if (
            not self._response_pending
            or self._response_active
            or self._speaking
            or self._pending_calls
            or self._turns_remaining == 0
            or self._closed
        ):
            return
        self._response_pending = False
        self._response_active = True
        self._turns_remaining -= 1
        await self._socket.response.create(response={"output_modalities": ["text"]})

    async def send_history(self, history: list[types.Content]) -> None:
        async with self._send_lock:
            for content in history:
                await self._create_content_items(content)
            if history and history[-1].role == "user":
                self._turns_remaining = self._config.max_response_turns
                self._response_pending = True
                await self._start_response_if_ready()

    async def send_content(self, content: types.Content) -> None:
        await self._send_content(content)

    async def _send_content(
        self, content: types.Content, *, partial: bool = False
    ) -> None:
        async with self._send_lock:
            await self._create_content_items(content)
            responses = [
                part.function_response
                for part in content.parts or []
                if part.function_response
            ]
            if responses:
                for response in responses:
                    if not response.will_continue:
                        self._pending_calls.discard(response.id or "")
                self._response_pending = True
            elif not partial:
                self._turns_remaining = self._config.max_response_turns
                self._response_pending = True
            # Passive context does not start a turn. Complete notifications from
            # async work get a bounded response budget just like user input.
            await self._start_response_if_ready()

    async def _create_content_items(self, content: types.Content) -> None:
        user_parts: list[Content] = []
        model_text: list[dict[str, str]] = []
        for part in content.parts or []:
            if part.function_response is not None:
                response = part.function_response
                if not response.id:
                    raise LiveAgentProviderError(
                        "OpenAI tool results require a call ID"
                    )
                await self._socket.conversation.item.create(
                    item={
                        "type": "function_call_output",
                        "call_id": response.id,
                        "output": json.dumps(response.response),
                    }
                )
            elif part.function_call is not None:
                call = part.function_call
                if not call.id or not call.name:
                    raise LiveAgentProviderError(
                        "OpenAI history tool calls require ID and name"
                    )
                await self._socket.conversation.item.create(
                    item={
                        "type": "function_call",
                        "call_id": call.id,
                        "name": call.name,
                        "arguments": json.dumps(call.args or {}),
                    }
                )
            elif part.text:
                if content.role == "model":
                    model_text.append({"type": "output_text", "text": part.text})
                else:
                    user_parts.append({"type": "input_text", "text": part.text})
            elif part.inline_data is not None:
                blob = part.inline_data
                if blob.mime_type in {"image/png", "image/jpeg"} and blob.data:
                    encoded = base64.b64encode(blob.data).decode("ascii")
                    user_parts.append(
                        {
                            "type": "input_image",
                            "image_url": f"data:{blob.mime_type};base64,{encoded}",
                            "detail": "low",
                        }
                    )
                else:
                    raise LiveAgentProviderError(
                        f"Unsupported OpenAI content: {blob.mime_type}"
                    )
            elif part.file_data is not None:
                raise LiveAgentProviderError(
                    "OpenAI Live input requires inline image data"
                )
        if user_parts:
            await self._socket.conversation.item.create(
                item={
                    "type": "message",
                    "role": "user",
                    "content": user_parts,
                }
            )
        if model_text:
            await self._socket.conversation.item.create(
                item={
                    "type": "message",
                    "role": "assistant",
                    "content": model_text,
                }
            )

    @singledispatchmethod
    async def send_realtime(
        self,
        blob: types.Blob
        | types.ActivityStart
        | types.ActivityEnd
        | types.LiveClientRealtimeInput,
    ) -> None:
        raise LiveAgentProviderError("Unsupported OpenAI realtime input")

    @send_realtime.register(types.ActivityStart)
    async def _activity_start(self, activity: types.ActivityStart) -> None:
        async with self._send_lock:
            if self._speaking:
                return
            self._speaking = True
            self._audio_bytes = 0
            self._resampler = av.AudioResampler(format="s16", layout="mono", rate=24000)
            await self._socket.input_audio_buffer.clear()

    async def _append_frames(self, frames: list[av.AudioFrame]) -> None:
        for frame in frames:
            data = bytes(frame.planes[0])[: frame.samples * 2]
            self._audio_bytes += len(data)
            await self._socket.input_audio_buffer.append(
                audio=base64.b64encode(data).decode("ascii")
            )

    @send_realtime.register(types.Blob)
    async def _audio_chunk(self, blob: types.Blob) -> None:
        if blob.mime_type not in {"audio/pcm;rate=16000", "audio/pcm;rate=24000"}:
            raise LiveAgentProviderError(
                f"Unsupported OpenAI realtime audio: {blob.mime_type}"
            )
        async with self._send_lock:
            if not self._speaking or not blob.data:
                return
            if len(blob.data) % 2:
                raise LiveAgentProviderError(
                    "PCM input must contain complete 16-bit samples"
                )
            frame = av.AudioFrame(
                format="s16", layout="mono", samples=len(blob.data) // 2
            )
            frame.planes[0].update(blob.data)
            frame.sample_rate = 16000 if blob.mime_type.endswith("16000") else 24000
            await self._append_frames(self._resampler.resample(frame))

    @send_realtime.register(types.ActivityEnd)
    async def _activity_end(self, activity: types.ActivityEnd) -> None:
        async with self._send_lock:
            if not self._speaking:
                return
            await self._append_frames(self._resampler.resample(None))
            self._speaking = False
            # The API requires at least 100 ms. Discard empty/short VAD glitches.
            if self._audio_bytes < 4800:
                await self._socket.input_audio_buffer.clear()
                await self._start_response_if_ready()
                return
            await self._socket.input_audio_buffer.commit()
            self._turns_remaining = self._config.max_response_turns
            self._response_pending = True
            await self._start_response_if_ready()

    @send_realtime.register(types.LiveClientRealtimeInput)
    async def _audio_stream_end(self, activity: types.LiveClientRealtimeInput) -> None:
        if activity.audio_stream_end:
            await self._activity_end(types.ActivityEnd())
        else:
            raise LiveAgentProviderError("Unsupported OpenAI realtime control event")

    def _usage_response(self, response: _Response) -> LlmResponse:
        usage = response.usage
        metadata = None
        if usage is not None:
            logger.info(
                "OpenAI Realtime usage session=%s response=%s model=%s usage=%s",
                self._session_id,
                response.id,
                self._model,
                usage.model_dump_json(),
            )
            metadata = types.GenerateContentResponseUsageMetadata(
                prompt_token_count=usage.input_tokens,
                candidates_token_count=usage.output_tokens,
                total_token_count=usage.total_tokens,
                cached_content_token_count=usage.input_token_details.cached_tokens,
            )
        return LlmResponse(
            usage_metadata=metadata,
            model_version=self._model,
            live_session_id=self._session_id,
            turn_complete=True,
            interaction_status=(
                types.InteractionStatus.IN_PROGRESS
                if (self._pending_calls and self._turns_remaining > 0)
                or self._response_active
                else types.InteractionStatus.IDLE
            ),
        )

    async def receive(self) -> AsyncGenerator[LlmResponse, None]:
        while not self._closed:
            event = _ServerEvent.model_validate_json(await self._socket.recv_bytes())
            if event.type == "error":
                self._raise_error(event.error)
            elif event.type == "response.output_audio.delta":
                raise LiveAgentProviderError(
                    "OpenAI returned audio despite text-only configuration"
                )
            elif event.type == "conversation.item.input_audio_transcription.completed":
                yield LlmResponse(
                    input_transcription=types.Transcription(
                        text=event.transcript, finished=True
                    ),
                    partial=False,
                    model_version=self._model,
                )
            elif event.type == "conversation.item.input_audio_transcription.failed":
                self._raise_error(event.error)
            elif event.type == "response.done" and event.response is not None:
                response = event.response
                if response.id in self._completed_responses:
                    continue
                self._completed_responses.add(response.id)
                async with self._send_lock:
                    self._response_active = False
                    if response.status not in {"completed", "cancelled"}:
                        details = response.status_details
                        self._raise_error(
                            details.error if details else _Error(code=response.status)
                        )
                    parts: list[types.Part] = []
                    if response.status == "completed":
                        for item in response.output:
                            if item.type == "function_call":
                                if not item.call_id or not item.name:
                                    raise LiveAgentProviderError(
                                        "OpenAI tool call missing ID or name"
                                    )
                                self._pending_calls.add(item.call_id)
                                parts.append(
                                    types.Part(
                                        function_call=types.FunctionCall(
                                            id=item.call_id,
                                            name=item.name,
                                            args=_ARGUMENTS.validate_json(
                                                item.arguments
                                            ),
                                        )
                                    )
                                )
                            elif item.type == "message":
                                parts.extend(
                                    types.Part(text=part.text)
                                    for part in item.content
                                    if part.type == "output_text" and part.text
                                )
                    await self._start_response_if_ready()
                completion = self._usage_response(response)
                completion.interrupted = response.status == "cancelled"
                if parts:
                    yield LlmResponse(
                        content=types.Content(role="model", parts=parts),
                        partial=False,
                        model_version=self._model,
                        live_session_id=self._session_id,
                    )
                yield completion
                return

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            await self._socket.close()
