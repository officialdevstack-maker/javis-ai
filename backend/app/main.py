import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import httpx
from fastapi import FastAPI, File, HTTPException, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .agent import AgentError, resolve_approval, run_agent
from .config import get_settings
from .db import (
    begin_chat_turn,
    complete_chat_turn,
    create_conversation,
    delete_conversation,
    discard_unanswered_turn,
    get_conversation,
    initialize_database,
    list_conversations,
)
from .llm import check_ollama
from .rag import RAGError, ingest_folder, list_indexed_sources, remove_indexed_source
from .schemas import (
    ChatRequest,
    ConfirmationRequest,
    ConfirmationResponse,
    ConversationDetail,
    ConversationSummary,
    HealthResponse,
    IngestRequest,
    IngestResponse,
    Message,
    SourcesResponse,
    SpeakRequest,
    TranscriptionResponse,
)
from .voice import VoiceError, synthesize, transcribe


settings = get_settings()
ingestion_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await asyncio.to_thread(initialize_database)
    yield


app = FastAPI(title="javis-ai", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)


def sse(event: str, data: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def event_stream(
    conversation_id: UUID,
    messages: list[Message],
) -> AsyncIterator[str]:
    answer: list[str] = []
    saved = False
    try:
        async for event in run_agent(messages):
            if event.event == "token":
                text = event.data.get("text")
                if isinstance(text, str):
                    answer.append(text)
            yield sse(event.event, event.data)
        if answer:
            await asyncio.to_thread(complete_chat_turn, conversation_id, "".join(answer))
            saved = True
        yield sse("done", {})
    except AgentError as exc:
        yield sse("error", {"message": str(exc)})
    except httpx.HTTPError as exc:
        yield sse("error", {"message": f"Connection to Ollama failed: {exc}"})
    except json.JSONDecodeError as exc:
        yield sse("error", {"message": f"Ollama returned invalid JSON: {exc}"})
    except sqlite3.Error as exc:
        yield sse("error", {"message": f"Could not save the assistant reply: {exc}"})
    finally:
        if not saved:
            try:
                if answer:
                    complete_chat_turn(conversation_id, "".join(answer))
                else:
                    discard_unanswered_turn(conversation_id)
            except sqlite3.Error:
                pass


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    reachable, model_available = await check_ollama()
    return HealthResponse(
        status="ok" if reachable and model_available else "degraded",
        ollama_reachable=reachable,
        model=settings.model,
        model_available=model_available,
    )


@app.get("/conversations", response_model=list[ConversationSummary])
async def conversations() -> list[ConversationSummary]:
    return await asyncio.to_thread(list_conversations)


@app.post(
    "/conversations",
    response_model=ConversationSummary,
    status_code=status.HTTP_201_CREATED,
)
async def new_conversation() -> ConversationSummary:
    return await asyncio.to_thread(create_conversation)


@app.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def conversation(conversation_id: UUID) -> ConversationDetail:
    result = await asyncio.to_thread(get_conversation, conversation_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return result


@app.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_conversation(conversation_id: UUID) -> Response:
    deleted = await asyncio.to_thread(delete_conversation, conversation_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/confirm", response_model=ConfirmationResponse)
async def confirm(request: ConfirmationRequest) -> ConfirmationResponse:
    if not resolve_approval(request.request_id, request.approved):
        raise HTTPException(status_code=404, detail="This confirmation request is no longer pending")
    return ConfirmationResponse(accepted=True)


@app.post("/ingest", response_model=IngestResponse)
async def ingest(request: IngestRequest) -> IngestResponse:
    try:
        async with ingestion_lock:
            return await asyncio.to_thread(
                ingest_folder,
                request.folder_path,
                request.scope,
            )
    except RAGError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/sources", response_model=SourcesResponse)
async def sources() -> SourcesResponse:
    return SourcesResponse(sources=await asyncio.to_thread(list_indexed_sources))


@app.delete("/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_source(source_id: str) -> Response:
    try:
        source = await asyncio.to_thread(remove_indexed_source, source_id)
    except RAGError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


MAX_AUDIO_BYTES = 10 * 1024 * 1024


@app.post("/voice/transcribe", response_model=TranscriptionResponse)
async def voice_transcribe(audio: UploadFile = File(...)) -> TranscriptionResponse:
    data = await audio.read()
    if not data:
        raise HTTPException(status_code=400, detail="The audio upload was empty")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="The audio upload is too large")
    try:
        text = await asyncio.to_thread(transcribe, data)
    except VoiceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return TranscriptionResponse(text=text)


@app.post("/voice/speak")
async def voice_speak(request: SpeakRequest) -> Response:
    try:
        wav = await asyncio.to_thread(synthesize, request.text)
    except VoiceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(content=wav, media_type="audio/wav")


@app.post("/chat")
async def chat(request: ChatRequest) -> StreamingResponse:
    messages = await asyncio.to_thread(
        begin_chat_turn,
        request.conversation_id,
        request.messages[-1],
    )
    if messages is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return StreamingResponse(
        event_stream(request.conversation_id, messages),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
