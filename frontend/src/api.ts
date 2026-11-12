export type Role = "user" | "assistant";

export interface ChatMessage {
  role: Role;
  content: string;
}

export interface ConversationSummary {
  id: string;
  title: string;
  scope: Scope;
  created_at: string;
  updated_at: string;
}

export interface Conversation extends ConversationSummary {
  messages: Array<ChatMessage & { id: number; created_at: string }>;
}

export type Scope = "private" | "public" | "business";

export interface MemoryItem {
  id: number;
  text: string;
  scope: Scope;
  created_at: string;
}

export interface ToolPolicy {
  name: string;
  tier: 0 | 1 | 2 | 3;
  requires_confirmation: boolean;
  is_read_only: boolean;
  tier3_opt_in: boolean;
  tier3_limits: Record<string, unknown> | null;
}

export interface AutonomyStatus {
  kill_switch_enabled: boolean;
  tools: ToolPolicy[];
}

export interface AuditEntry {
  id: number;
  tool_name: string;
  scope: Scope;
  tier: 0 | 1 | 2 | 3;
  arguments: Record<string, unknown>;
  result: string | null;
  status: "running" | "completed" | "denied" | "blocked" | "error";
  approval_by: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface IndexedSource {
  id: string;
  path: string;
  folder_path: string;
  source: string;
  scope: Scope;
  indexed_at: string;
  chunk_count: number;
}

export interface IngestResult {
  indexed_files: number;
  indexed_chunks: number;
  sources: IndexedSource[];
}

export interface StreamHandlers {
  onToken: (text: string) => void;
  onError: (message: string) => void;
  onToolCall: (tool: ToolCallEvent) => void;
  onToolResult: (tool: ToolResultEvent) => void;
  onConfirmation: (request: ConfirmationEvent) => void;
}

export interface ToolCallEvent {
  tool_id: string;
  name: string;
  arguments: Record<string, unknown>;
}

export interface ToolResultEvent extends ToolCallEvent {
  result: string;
}

export interface ConfirmationEvent extends ToolCallEvent {
  request_id: string;
}

interface SseEvent {
  event: string;
  data: unknown;
}

const API_URL = import.meta.env.VITE_API_URL;

function apiUrl(path: string): string {
  if (!API_URL) {
    throw new Error("Set VITE_API_URL in frontend/.env to connect to the backend.");
  }
  return `${API_URL.replace(/\/+$/, "")}${path}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), init);
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(
      detail
        ? `Backend error (HTTP ${response.status}): ${detail}`
        : `The backend returned HTTP ${response.status}.`,
    );
  }
  return (await response.json()) as T;
}

export function listConversations(scope?: Scope): Promise<ConversationSummary[]> {
  const search = scope ? `?scope=${encodeURIComponent(scope)}` : "";
  return request<ConversationSummary[]>(`/conversations${search}`);
}

export function createConversation(scope: Scope = "private"): Promise<ConversationSummary> {
  return request<ConversationSummary>("/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scope }),
  });
}

export function getConversation(id: string): Promise<Conversation> {
  return request<Conversation>(`/conversations/${encodeURIComponent(id)}`);
}

export async function deleteConversation(id: string): Promise<void> {
  const response = await fetch(apiUrl(`/conversations/${encodeURIComponent(id)}`), {
    method: "DELETE",
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(
      detail
        ? `Backend error (HTTP ${response.status}): ${detail}`
        : `The backend returned HTTP ${response.status}.`,
    );
  }
}

export function listMemories(scope: Scope = "private"): Promise<MemoryItem[]> {
  return request<MemoryItem[]>(`/memories?scope=${encodeURIComponent(scope)}`);
}

export function createMemory(fact: string, scope: Scope = "private"): Promise<MemoryItem> {
  return request<MemoryItem>("/memories", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text: fact, scope }),
  });
}

export async function deleteMemory(id: number): Promise<void> {
  const response = await fetch(apiUrl(`/memories/${encodeURIComponent(String(id))}`), {
    method: "DELETE",
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(
      detail
        ? `Backend error (HTTP ${response.status}): ${detail}`
        : `The backend returned HTTP ${response.status}.`,
    );
  }
}

export function getAutonomyStatus(): Promise<AutonomyStatus> {
  return request<AutonomyStatus>("/autonomy");
}

export function updateKillSwitch(enabled: boolean): Promise<AutonomyStatus> {
  return request<AutonomyStatus>("/autonomy/kill-switch", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
}

export function getAuditLog(
  scope?: Scope,
  toolName?: string,
): Promise<AuditEntry[]> {
  const search = new URLSearchParams({ limit: "200" });
  if (scope) search.set("scope", scope);
  if (toolName) search.set("tool_name", toolName);
  return request<AuditEntry[]>(`/audit?${search.toString()}`);
}

export function getSources(): Promise<{ sources: IndexedSource[] }> {
  return request<{ sources: IndexedSource[] }>("/sources");
}

export function ingestFolder(folderPath: string, scope: Scope): Promise<IngestResult> {
  return request<IngestResult>("/ingest", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ folder_path: folderPath, scope }),
  });
}

export async function removeSource(id: string): Promise<void> {
  const response = await fetch(apiUrl(`/sources/${encodeURIComponent(id)}`), {
    method: "DELETE",
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(
      detail
        ? `Backend error (HTTP ${response.status}): ${detail}`
        : `The backend returned HTTP ${response.status}.`,
    );
  }
}

export function confirmToolCall(requestId: string, approved: boolean): Promise<{ accepted: boolean }> {
  return request<{ accepted: boolean }>("/confirm", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ request_id: requestId, approved }),
  });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseSseEvent(raw: string): SseEvent | null {
  let event = "message";
  const data: string[] = [];

  for (const line of raw.split(/\r?\n/)) {
    if (line.startsWith("event:")) {
      event = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      data.push(line.slice("data:".length).trimStart());
    }
  }

  if (data.length === 0) return null;

  try {
    return { event, data: JSON.parse(data.join("\n")) as unknown };
  } catch {
    return null;
  }
}

function dispatch(
  raw: string,
  handlers: StreamHandlers,
  markComplete: () => void,
): void {
  const message = parseSseEvent(raw);
  if (!message || !isRecord(message.data)) return;

  if (message.event === "token" && typeof message.data.text === "string") {
    handlers.onToken(message.data.text);
  } else if (message.event === "error") {
    handlers.onError(
      typeof message.data.message === "string"
        ? message.data.message
        : "The backend reported an unknown error.",
    );
  } else if (
    (message.event === "tool_call" || message.event === "tool_result" || message.event === "confirm_request") &&
    typeof message.data.tool_id === "string" &&
    typeof message.data.name === "string" &&
    isRecord(message.data.arguments)
  ) {
    const tool = {
      tool_id: message.data.tool_id,
      name: message.data.name,
      arguments: message.data.arguments,
    };
    if (message.event === "tool_call") {
      handlers.onToolCall(tool);
    } else if (message.event === "tool_result" && typeof message.data.result === "string") {
      handlers.onToolResult({ ...tool, result: message.data.result });
    } else if (message.event === "confirm_request" && typeof message.data.request_id === "string") {
      handlers.onConfirmation({ ...tool, request_id: message.data.request_id });
    }
  } else if (message.event === "done") {
    markComplete();
  }
}

export async function transcribeAudio(audio: Blob): Promise<string> {
  const form = new FormData();
  form.append("audio", audio, "speech.webm");
  const response = await fetch(apiUrl("/voice/transcribe"), { method: "POST", body: form });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(
      detail
        ? `Backend error (HTTP ${response.status}): ${detail}`
        : `The backend returned HTTP ${response.status}.`,
    );
  }
  const result = (await response.json()) as { text: string };
  return result.text;
}

export async function synthesizeSpeech(text: string): Promise<Blob> {
  const response = await fetch(apiUrl("/voice/speak"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(
      detail
        ? `Backend error (HTTP ${response.status}): ${detail}`
        : `The backend returned HTTP ${response.status}.`,
    );
  }
  return response.blob();
}

export async function streamChat(
  conversationId: string,
  messages: ChatMessage[],
  handlers: StreamHandlers,
  signal: AbortSignal,
): Promise<void> {
  let completed = false;
  let reportedError = false;
  const reportError = (message: string): void => {
    reportedError = true;
    handlers.onError(message);
  };
  const markComplete = (): void => {
    completed = true;
  };

  try {
    const response = await fetch(apiUrl("/chat"), {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({ conversation_id: conversationId, messages }),
      signal,
    });

    if (!response.ok) {
      const detail = await response.text();
      reportError(
        detail
          ? `Backend error (HTTP ${response.status}): ${detail}`
          : `The backend returned HTTP ${response.status}.`,
      );
      return;
    }
    if (!response.body) {
      reportError("The backend response did not include a readable stream.");
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      let separator = buffer.search(/\r?\n\r?\n/);
      while (separator !== -1) {
        const delimiter = buffer.slice(separator).match(/^\r?\n\r?\n/)?.[0] ?? "\n\n";
        dispatch(buffer.slice(0, separator), { ...handlers, onError: reportError }, markComplete);
        buffer = buffer.slice(separator + delimiter.length);
        separator = buffer.search(/\r?\n\r?\n/);
      }
    }

    buffer += decoder.decode();
    if (buffer.trim()) {
      dispatch(buffer, { ...handlers, onError: reportError }, markComplete);
    }

    if (!signal.aborted && !completed && !reportedError) {
      reportError("The backend stream ended before the reply was complete.");
    }
  } catch (error: unknown) {
    if (signal.aborted || (error instanceof DOMException && error.name === "AbortError")) {
      return;
    }
    reportError(
      error instanceof Error
        ? `Could not connect to the backend: ${error.message}`
        : "Could not connect to the backend.",
    );
  }
}
