import asyncio
import json
import sqlite3
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import httpx
from fastapi import FastAPI, File, HTTPException, Query, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .agent import AgentError, resolve_approval, run_agent
from .autonomy import set_kill_switch, status as autonomy_status
from .config import get_settings
from .db import (
    add_memory,
    begin_chat_turn,
    complete_chat_turn,
    create_conversation,
    delete_conversation,
    delete_memory,
    discard_unanswered_turn,
    get_conversation,
    initialize_database,
    list_conversations,
    list_memories,
    list_tool_audit,
)
from .llm import check_ollama
from .rag import RAGError, ingest_folder, list_indexed_sources, remove_indexed_source
from .schemas import (
    AuditEntry,
    AutonomyStatus,
    ChatRequest,
    ConfirmationRequest,
    ConfirmationResponse,
    ConversationDetail,
    ConversationSummary,
    HealthResponse,
    IngestRequest,
    IngestResponse,
    KillSwitchRequest,
    MemoryCreate,
    MemoryItem,
    Message,
    NewConversationRequest,
    Scope,
    SourcesResponse,
    SpeakRequest,
    TranscriptionResponse,
)
from .voice import VoiceError, synthesize, transcribe


settings = get_settings()
ingestion_lock = asyncio.Lock()


def _warm_up() -> None:
    from .voice import _voice, _whisper

    try:
        _whisper()
    except Exception:
        pass
    try:
        _voice()
    except Exception:
        pass
    try:
        httpx.post(
            f"{settings.ollama_url}/api/chat",
            json={"model": settings.model, "messages": [], "keep_alive": "30m"},
            timeout=120,
        )
    except httpx.HTTPError:
        pass


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await asyncio.to_thread(initialize_database)
    await asyncio.to_thread(_warm_up)
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
    scope: str = "private",
) -> AsyncIterator[str]:
    answer: list[str] = []
    saved = False
    started = time.perf_counter()
    first = True
    try:
        async for event in run_agent(messages, scope=scope):
            if event.event == "token":
                text = event.data.get("text")
                if isinstance(text, str):
                    answer.append(text)
                    if first:
                        print(f"[chat] first token {time.perf_counter() - started:.2f}s")
                        first = False
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
async def conversations(scope: Scope | None = None) -> list[ConversationSummary]:
    return await asyncio.to_thread(list_conversations, scope)


@app.post(
    "/conversations",
    response_model=ConversationSummary,
    status_code=status.HTTP_201_CREATED,
)
async def new_conversation(request: NewConversationRequest | None = None) -> ConversationSummary:
    requested_scope = request.scope if request is not None else "private"
    return await asyncio.to_thread(create_conversation, requested_scope)


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


@app.get("/memories", response_model=list[MemoryItem])
async def memories(scope: Scope | None = None) -> list[MemoryItem]:
    return await asyncio.to_thread(list_memories, scope)


@app.post("/memories", response_model=MemoryItem, status_code=status.HTTP_201_CREATED)
async def create_memory(request: MemoryCreate) -> MemoryItem:
    return await asyncio.to_thread(add_memory, request.text, request.scope)


@app.delete("/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_memory(memory_id: int) -> Response:
    deleted = await asyncio.to_thread(delete_memory, memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/autonomy", response_model=AutonomyStatus)
async def get_autonomy_status() -> AutonomyStatus:
    return await asyncio.to_thread(autonomy_status)


@app.put("/autonomy/kill-switch", response_model=AutonomyStatus)
async def update_kill_switch(request: KillSwitchRequest) -> AutonomyStatus:
    await asyncio.to_thread(set_kill_switch, request.enabled)
    return await asyncio.to_thread(autonomy_status)


@app.get("/audit", response_model=list[AuditEntry])
async def audit_log(
    scope: Scope | None = None,
    tool_name: str | None = Query(default=None, min_length=1, max_length=100),
    limit: int = Query(default=100, ge=1, le=200),
) -> list[AuditEntry]:
    return await asyncio.to_thread(
        list_tool_audit,
        scope=scope,
        tool_name=tool_name,
        limit=limit,
    )


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
    started = time.perf_counter()
    data = await audio.read()
    if not data:
        raise HTTPException(status_code=400, detail="The audio upload was empty")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="The audio upload is too large")
    try:
        text = await asyncio.to_thread(transcribe, data)
    except VoiceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    print(f"[voice] transcribe {time.perf_counter() - started:.2f}s -> {text!r}")
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
    turn = await asyncio.to_thread(
        begin_chat_turn,
        request.conversation_id,
        request.messages[-1],
    )
    if turn is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages, scope = turn
    return StreamingResponse(
        event_stream(request.conversation_id, messages, scope),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
