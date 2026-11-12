from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


Scope = Literal["private", "public", "business"]
ToolTier = Literal[0, 1, 2, 3]


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class StoredMessage(Message):
    id: int
    created_at: datetime


class ChatRequest(BaseModel):
    conversation_id: UUID
    messages: list[Message] = Field(min_length=1)

    @model_validator(mode="after")
    def require_latest_user_message(self) -> "ChatRequest":
        if self.messages[-1].role != "user":
            raise ValueError("The last message must be from the user")
        return self


class ConversationSummary(BaseModel):
    id: UUID
    title: str
    scope: Scope = "private"
    created_at: datetime
    updated_at: datetime


class MemoryItem(BaseModel):
    id: int
    text: str
    scope: Scope
    created_at: datetime


class MemoryCreate(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    scope: Scope = "private"


class NewConversationRequest(BaseModel):
    scope: Scope = "private"


class ConversationDetail(ConversationSummary):
    messages: list[StoredMessage]


class ConfirmationRequest(BaseModel):
    request_id: UUID
    approved: bool


class ConfirmationResponse(BaseModel):
    accepted: bool


class IngestRequest(BaseModel):
    folder_path: str = Field(min_length=1, max_length=2048)
    scope: Scope = "private"


class IndexedSource(BaseModel):
    id: str
    path: str
    folder_path: str
    source: str
    scope: Scope
    indexed_at: datetime
    chunk_count: int = Field(ge=0)


class IngestResponse(BaseModel):
    indexed_files: int
    indexed_chunks: int
    sources: list[IndexedSource]


class SourcesResponse(BaseModel):
    sources: list[IndexedSource]


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    ollama_reachable: bool
    model: str
    model_available: bool


class TranscriptionResponse(BaseModel):
    text: str


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class KillSwitchRequest(BaseModel):
    enabled: bool


class ToolPolicyInfo(BaseModel):
    name: str
    tier: ToolTier
    requires_confirmation: bool
    is_read_only: bool
    tier3_opt_in: bool
    tier3_limits: dict[str, object] | None = None


class AutonomyStatus(BaseModel):
    kill_switch_enabled: bool
    tools: list[ToolPolicyInfo]


class AuditEntry(BaseModel):
    id: int
    tool_name: str
    scope: Scope
    tier: ToolTier
    arguments: dict[str, object]
    result: str | None
    status: Literal["running", "completed", "denied", "blocked", "error"]
    approval_by: str | None
    created_at: datetime
    completed_at: datetime | None
