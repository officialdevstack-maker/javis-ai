import { Fragment, useEffect, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent } from "react";
import ReactMarkdown from "react-markdown";
import {
  confirmToolCall,
  createConversation,
  deleteConversation,
  deleteMemory,
  getConversation,
  listConversations,
  listMemories,
  streamChat,
} from "./api";
import type {
  ChatMessage,
  ConfirmationEvent,
  ConversationSummary,
  MemoryItem,
  Scope,
  ToolCallEvent,
  ToolResultEvent,
} from "./api";

type ToolStatus = "running" | "awaiting_confirmation" | "approved" | "denied" | "complete";

interface ToolActivity extends ToolCallEvent {
  status: ToolStatus;
  result?: string;
  requestId?: string;
}

interface ChatProps {
  onOpenSources: () => void;
  onOpenVoice: () => void;
  onOpenSafety: () => void;
}

function updateToolActivity(
  current: ToolActivity[],
  toolId: string,
  update: Partial<ToolActivity>,
): ToolActivity[] {
  return current.map((activity) =>
    activity.tool_id === toolId ? { ...activity, ...update } : activity,
  );
}

function appendToLast(messages: ChatMessage[], chunk: string): ChatMessage[] {
  const last = messages.at(-1);
  if (!last || last.role !== "assistant") return messages;
  return [...messages.slice(0, -1), { ...last, content: last.content + chunk }];
}

function removeEmptyAssistant(messages: ChatMessage[]): ChatMessage[] {
  const last = messages.at(-1);
  return last?.role === "assistant" && !last.content
    ? messages.slice(0, -1)
    : messages;
}

export default function Chat({ onOpenSources, onOpenVoice, onOpenSafety }: ChatProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [scope, setScope] = useState<Scope>("private");
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [loadingConversation, setLoadingConversation] = useState(true);
  const [toolActivities, setToolActivities] = useState<ToolActivity[]>([]);
  const [confirmingToolId, setConfirmingToolId] = useState<string | null>(null);
  const abortController = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const selectionRequest = useRef(0);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  useEffect(() => {
    let active = true;
    async function loadHistory(): Promise<void> {
      try {
        const [history, memoryItems] = await Promise.all([listConversations(scope), listMemories(scope)]);
        if (!active) return;
        setConversations(history);
        setMemories(memoryItems);
        const mostRecent = history[0];
        if (mostRecent) {
          const conversation = await getConversation(mostRecent.id);
          if (!active) return;
          setConversationId(conversation.id);
          setMessages(conversation.messages);
          setToolActivities([]);
        } else {
          setConversationId(null);
          setMessages([]);
        }
      } catch (loadError: unknown) {
        if (active) {
          setError(loadError instanceof Error ? loadError.message : "Could not load conversations.");
        }
      } finally {
        if (active) setLoadingConversation(false);
      }
    }

    void loadHistory();
    return () => {
      active = false;
    };
  }, [scope]);

  async function openConversation(id: string): Promise<void> {
    if (streaming || loadingConversation) return;
    const requestId = ++selectionRequest.current;
    setLoadingConversation(true);
    setConversationId(id);
    setMessages([]);
    setToolActivities([]);
    setError(null);
    try {
      const conversation = await getConversation(id);
      if (requestId !== selectionRequest.current) return;
      setMessages(conversation.messages);
    } catch (loadError: unknown) {
      if (requestId === selectionRequest.current) {
        setError(loadError instanceof Error ? loadError.message : "Could not load this conversation.");
      }
    } finally {
      if (requestId === selectionRequest.current) setLoadingConversation(false);
    }
  }

  async function sendMessage(): Promise<void> {
    const content = input.trim();
    if (!content || streaming || loadingConversation) return;

    const history: ChatMessage[] = [...messages, { role: "user", content }];
    setMessages([...history, { role: "assistant", content: "" }]);
    setToolActivities([]);
    setInput("");
    setError(null);

    try {
      const activeConversation = conversationId ?? (await createConversation(scope)).id;
      if (!conversationId) {
        setConversationId(activeConversation);
      }
      setStreaming(true);

      const controller = new AbortController();
      abortController.current = controller;
      await streamChat(
        activeConversation,
        history,
        {
          onToken: (token) => setMessages((current) => appendToLast(current, token)),
          onError: setError,
          onToolCall: (tool: ToolCallEvent) => {
            setToolActivities((current) => [...current, { ...tool, status: "running" }]);
          },
          onConfirmation: (request: ConfirmationEvent) => {
            setToolActivities((current) =>
              updateToolActivity(current, request.tool_id, {
                status: "awaiting_confirmation",
                requestId: request.request_id,
              }),
            );
          },
          onToolResult: (tool: ToolResultEvent) => {
            setToolActivities((current) =>
              updateToolActivity(current, tool.tool_id, {
                status: tool.result.startsWith("Denied") ? "denied" : "complete",
                result: tool.result,
              }),
            );
          },
        },
        controller.signal,
      );

      if (abortController.current === controller) {
        abortController.current = null;
        setStreaming(false);
        setMessages((current) => removeEmptyAssistant(current));
        inputRef.current?.focus();
        setConversations(await listConversations());
      }
    } catch (sendError: unknown) {
      setError(sendError instanceof Error ? sendError.message : "Could not send this message.");
      setMessages((current) => removeEmptyAssistant(current));
      setStreaming(false);
    }
  }

  function stopStreaming(): void {
    abortController.current?.abort();
  }

  async function startNewChat(): Promise<void> {
    if (streaming || loadingConversation) return;
    ++selectionRequest.current;
    setError(null);
    setLoadingConversation(true);
    try {
      const conversation = await createConversation(scope);
      setConversations((current) => [conversation, ...current]);
      setConversationId(conversation.id);
      setMessages([]);
      setToolActivities([]);
      inputRef.current?.focus();
    } catch (createError: unknown) {
      setError(createError instanceof Error ? createError.message : "Could not create a conversation.");
    } finally {
      setLoadingConversation(false);
    }
  }

  async function removeConversation(id: string, title: string): Promise<void> {
    if (!window.confirm(`Delete "${title}" and all its messages? This cannot be undone.`)) return;
    try {
      await deleteConversation(id);
      const remaining = await listConversations(scope);
      setConversations(remaining);
      if (conversationId === id) {
        ++selectionRequest.current;
        setConversationId(null);
        setMessages([]);
        setToolActivities([]);
      }
    } catch (deleteError: unknown) {
      setError(deleteError instanceof Error ? deleteError.message : "Could not delete this conversation.");
    }
  }

  async function removeMemory(memoryId: number): Promise<void> {
    try {
      await deleteMemory(memoryId);
      setMemories((current) => current.filter((memory) => memory.id !== memoryId));
    } catch (deleteError: unknown) {
      setError(deleteError instanceof Error ? deleteError.message : "Could not delete this memory.");
    }
  }

  async function respondToConfirmation(activity: ToolActivity, approved: boolean): Promise<void> {
    if (!activity.requestId) return;
    setConfirmingToolId(activity.tool_id);
    setError(null);
    try {
      await confirmToolCall(activity.requestId, approved);
      setToolActivities((current) =>
        updateToolActivity(current, activity.tool_id, {
          status: approved ? "approved" : "denied",
        }),
      );
    } catch (confirmationError: unknown) {
      setError(
        confirmationError instanceof Error
          ? confirmationError.message
          : "Could not submit the tool confirmation.",
      );
    } finally {
      setConfirmingToolId(null);
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    void sendMessage();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>): void {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void sendMessage();
    }
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="Javis home">
          <span className="brand-mark" aria-hidden="true">J</span>
          <span>javis<span className="brand-ai">.ai</span></span>
        </a>
        <div className="header-actions">
          <label className="scope-picker" aria-label="Select active scope">
            <span>Scope</span>
            <select
              value={scope}
              onChange={(event) => setScope(event.target.value as Scope)}
              aria-label="Scope"
            >
              <option value="private">Private</option>
              <option value="public">Public</option>
              <option value="business">Business</option>
            </select>
          </label>
          <span className="local-indicator"><span /> Local and private</span>
          <button className="new-chat-button" onClick={onOpenVoice}>
            Voice
          </button>
          <button className="new-chat-button" onClick={onOpenSources}>
            Sources
          </button>
          <button className="new-chat-button" onClick={onOpenSafety}>
            Safety
          </button>
          <button className="new-chat-button" onClick={() => void startNewChat()} disabled={streaming || loadingConversation}>
            <span aria-hidden="true">+</span> New chat
          </button>
        </div>
      </header>

      <div className="workspace">
        <aside className="sidebar" aria-label="Conversation history">
          <p className="sidebar-heading">YOUR CHATS</p>
          <button className="sidebar-new-chat" onClick={() => void startNewChat()} disabled={streaming || loadingConversation}>
            <span aria-hidden="true">+</span> New conversation
          </button>
          <div className="conversation-list">
            {conversations.map((conversation) => (
              <div
                className={`conversation-entry${conversation.id === conversationId ? " active" : ""}`}
                key={conversation.id}
              >
                <button
                  className="conversation-select"
                  onClick={() => void openConversation(conversation.id)}
                  disabled={streaming || loadingConversation}
                  title={conversation.title}
                >
                  <span className="conversation-icon" aria-hidden="true">◇</span>
                  <span>{conversation.title}</span>
                </button>
                <button
                  className="conversation-delete"
                  onClick={() => void removeConversation(conversation.id, conversation.title)}
                  disabled={streaming || loadingConversation}
                  aria-label={`Delete ${conversation.title}`}
                  title="Delete conversation"
                >
                  ×
                </button>
              </div>
            ))}
            {!loadingConversation && conversations.length === 0 && (
              <p className="no-conversations">Your conversations will appear here.</p>
            )}
          </div>
          <div className="memory-panel">
            <p className="sidebar-heading">MEMORIES</p>
            {memories.length === 0 ? (
              <p className="no-memories">No saved memories in {scope}.</p>
            ) : (
              <ul className="memory-list">
                {memories.map((memory) => (
                  <li key={memory.id} className="memory-item">
                    <span>{memory.text}</span>
                    <button
                      type="button"
                      className="memory-delete"
                      onClick={() => void removeMemory(memory.id)}
                      aria-label={`Delete memory ${memory.id}`}
                      title="Delete memory"
                    >
                      ×
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div className="sidebar-footer">
            <span className="sidebar-local-dot" />
            Stored on this device
          </div>
        </aside>

        <section className="chat-panel" aria-label="Chat with Javis">
        <div className="messages" aria-live="polite">
          {messages.length === 0 ? (
            <div className="welcome">
              <span className="welcome-orb" aria-hidden="true">
                <span />
                <span />
                <span />
              </span>
              <p className="eyebrow">YOUR LOCAL AI ASSISTANT</p>
              <h1>What can I help with?</h1>
              <p className="welcome-copy">Ask a question, explore an idea, or start with whatever is on your mind.</p>
            </div>
          ) : (
            <div className="conversation">
              {messages.map((message, index) => (
                <Fragment key={index}>
                  {index === messages.length - 1 && message.role === "assistant" && (
                    <div className="tool-activity-list" aria-live="polite">
                      {toolActivities.map((activity) => (
                        <article className="tool-activity" key={activity.tool_id}>
                          <div className="tool-activity-heading">
                            <span className="tool-activity-icon" aria-hidden="true">⌘</span>
                            <span>Used <strong>{activity.name}</strong></span>
                            <span className={`tool-status ${activity.status}`}>
                              {activity.status === "awaiting_confirmation"
                                ? "Approval needed"
                                : activity.status === "approved"
                                  ? "Approved"
                                  : activity.status === "denied"
                                    ? "Denied"
                                    : activity.status === "complete"
                                      ? "Complete"
                                      : "Running"}
                            </span>
                          </div>
                          <details className="tool-details" open>
                            <summary>Arguments and result</summary>
                            <pre>{JSON.stringify(activity.arguments, null, 2)}</pre>
                            {activity.result && <pre>{activity.result}</pre>}
                          </details>
                          {activity.status === "awaiting_confirmation" && (
                            <div className="tool-approval">
                              <span>Javis is asking to make this change.</span>
                              <button
                                className="approval-deny"
                                onClick={() => void respondToConfirmation(activity, false)}
                                disabled={confirmingToolId === activity.tool_id}
                              >
                                Deny
                              </button>
                              <button
                                className="approval-allow"
                                onClick={() => void respondToConfirmation(activity, true)}
                                disabled={confirmingToolId === activity.tool_id}
                              >
                                Approve
                              </button>
                            </div>
                          )}
                        </article>
                      ))}
                    </div>
                  )}
                  <article className={`message-row ${message.role}`}>
                    {message.role === "assistant" && (
                      <span className="assistant-avatar" aria-hidden="true">J</span>
                    )}
                    <div className={`message-bubble ${message.role}`}>
                      {message.role === "assistant" ? (
                        message.content ? (
                          <ReactMarkdown>{message.content}</ReactMarkdown>
                        ) : (
                          <span className="thinking"><span /><span /><span /></span>
                        )
                      ) : (
                        message.content
                      )}
                    </div>
                  </article>
                </Fragment>
              ))}
              <div ref={bottomRef} />
            </div>
          )}
          {error && <p className="error-message" role="alert">{error}</p>}
        </div>

        <div className="composer-wrap">
          {messages.length === 0 && (
            <div className="suggestions" aria-label="Suggested prompts">
              <button onClick={() => setInput("Help me plan my day")} type="button">
                <span aria-hidden="true">✳</span> Help me plan my day
              </button>
              <button onClick={() => setInput("Explain a topic I'm curious about")} type="button">
                <span aria-hidden="true">⌕</span> Explain a topic
              </button>
            </div>
          )}
          <form className="composer" onSubmit={handleSubmit}>
            <textarea
              ref={inputRef}
              aria-label="Message Javis"
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Message Javis..."
              rows={1}
              disabled={streaming || loadingConversation}
            />
            {streaming ? (
              <button className="send-button stop-button" type="button" onClick={stopStreaming} aria-label="Stop generating">
                <span className="stop-icon" />
              </button>
            ) : (
              <button className="send-button" type="submit" disabled={!input.trim()} aria-label="Send message">
                <span aria-hidden="true">↑</span>
              </button>
            )}
          </form>
          <p className="composer-hint">Enter to send <span>·</span> Shift + Enter for a new line</p>
        </div>
        </section>
      </div>
    </main>
  );
}
