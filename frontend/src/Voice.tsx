import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import {
  confirmToolCall,
  createConversation,
  streamChat,
  synthesizeSpeech,
  transcribeAudio,
} from "./api";
import type { ChatMessage, ConfirmationEvent } from "./api";

type VoiceState = "idle" | "listening" | "thinking" | "speaking";

interface VoiceProps {
  onOpenChat: () => void;
}

const SENTENCE_END = /^[\s\S]*?[.!?]["')\]]*\s+/;
const MIN_AUDIO_BYTES = 1500;

const STATUS_LABEL: Record<VoiceState, string> = {
  idle: "HOLD SPACE TO TALK",
  listening: "LISTENING",
  thinking: "THINKING",
  speaking: "SPEAKING",
};

function cleanForSpeech(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, " code omitted. ")
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\[[^\]]*\]/g, "")
    .replace(/[*_`#>]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return true;
  return target.tagName === "BUTTON" && target.dataset.voiceOrb === undefined;
}

export default function Voice({ onOpenChat }: VoiceProps) {
  const [state, setState] = useState<VoiceState>("idle");
  const [heard, setHeard] = useState("");
  const [reply, setReply] = useState("");
  const [toolName, setToolName] = useState<string | null>(null);
  const [approval, setApproval] = useState<ConfirmationEvent | null>(null);
  const [error, setError] = useState<string | null>(null);

  const stateRef = useRef<VoiceState>("idle");
  const holding = useRef(false);
  const recorder = useRef<MediaRecorder | null>(null);
  const generation = useRef(0);
  const abort = useRef<AbortController | null>(null);
  const playbackChain = useRef(Promise.resolve());
  const stopCurrentAudio = useRef<(() => void) | null>(null);
  const conversationId = useRef<string | null>(null);
  const history = useRef<ChatMessage[]>([]);
  const handlers = useRef<{ start: () => void; stop: () => void; interrupt: () => void }>({
    start: () => {},
    stop: () => {},
    interrupt: () => {},
  });

  function changeState(next: VoiceState): void {
    stateRef.current = next;
    setState(next);
  }

  function playBlob(blob: Blob): Promise<void> {
    return new Promise((resolve) => {
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      const finish = (): void => {
        URL.revokeObjectURL(url);
        stopCurrentAudio.current = null;
        resolve();
      };
      audio.onended = finish;
      audio.onerror = finish;
      stopCurrentAudio.current = () => {
        audio.pause();
        finish();
      };
      changeState("speaking");
      void audio.play().catch(finish);
    });
  }

  function enqueueSpeech(text: string, gen: number): void {
    const clean = cleanForSpeech(text);
    if (!clean) return;
    const audioPromise = synthesizeSpeech(clean).catch(() => null);
    playbackChain.current = playbackChain.current.then(async () => {
      if (gen !== generation.current) return;
      const blob = await audioPromise;
      if (!blob || gen !== generation.current) return;
      await playBlob(blob);
    });
  }

  function interrupt(): void {
    generation.current += 1;
    abort.current?.abort();
    abort.current = null;
    stopCurrentAudio.current?.();
    playbackChain.current = Promise.resolve();
    setApproval(null);
    setToolName(null);
    changeState("idle");
  }

  async function runTurn(text: string, gen: number): Promise<void> {
    if (!conversationId.current) {
      conversationId.current = (await createConversation()).id;
    }
    const messages: ChatMessage[] = [...history.current, { role: "user", content: text }];
    const controller = new AbortController();
    abort.current = controller;
    let answer = "";
    let pending = "";

    const drain = (final: boolean): void => {
      let match = SENTENCE_END.exec(pending);
      while (match) {
        enqueueSpeech(match[0], gen);
        pending = pending.slice(match[0].length);
        match = SENTENCE_END.exec(pending);
      }
      if (final && pending.trim()) {
        enqueueSpeech(pending, gen);
        pending = "";
      }
    };

    await streamChat(
      conversationId.current,
      messages,
      {
        onToken: (token) => {
          if (gen !== generation.current) return;
          answer += token;
          pending += token;
          setReply(answer);
          drain(false);
        },
        onError: (message) => setError(message),
        onToolCall: (tool) => setToolName(tool.name),
        onToolResult: () => setToolName(null),
        onConfirmation: (request) => {
          setApproval(request);
          enqueueSpeech("I need your approval to continue.", gen);
        },
      },
      controller.signal,
    );

    if (gen !== generation.current) return;
    drain(true);
    if (answer) {
      history.current = [...messages, { role: "assistant", content: answer }];
    }
    await playbackChain.current;
    if (gen === generation.current) changeState("idle");
  }

  async function handleUtterance(audio: Blob): Promise<void> {
    if (audio.size < MIN_AUDIO_BYTES) {
      changeState("idle");
      return;
    }
    const gen = ++generation.current;
    try {
      const text = await transcribeAudio(audio);
      if (gen !== generation.current) return;
      if (!text) {
        setHeard("");
        setReply("Sorry, I didn't catch that.");
        changeState("idle");
        return;
      }
      setHeard(text);
      setReply("");
      await runTurn(text, gen);
    } catch (turnError: unknown) {
      if (gen === generation.current) {
        setError(turnError instanceof Error ? turnError.message : "Something went wrong.");
        changeState("idle");
      }
    }
  }

  async function startListening(): Promise<void> {
    if (holding.current) return;
    if (stateRef.current !== "idle") interrupt();
    holding.current = true;
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!holding.current) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      const chunks: Blob[] = [];
      const rec = new MediaRecorder(stream);
      rec.ondataavailable = (event) => {
        if (event.data.size > 0) chunks.push(event.data);
      };
      rec.onstop = () => {
        stream.getTracks().forEach((track) => track.stop());
        void handleUtterance(new Blob(chunks, { type: rec.mimeType }));
      };
      recorder.current = rec;
      rec.start();
      changeState("listening");
    } catch (micError: unknown) {
      holding.current = false;
      setError(
        micError instanceof Error
          ? `Microphone unavailable: ${micError.message}`
          : "Microphone unavailable.",
      );
    }
  }

  function stopListening(): void {
    holding.current = false;
    const rec = recorder.current;
    recorder.current = null;
    if (rec && rec.state === "recording") {
      changeState("thinking");
      rec.stop();
    }
  }

  async function respondToApproval(approved: boolean): Promise<void> {
    if (!approval) return;
    const request = approval;
    setApproval(null);
    try {
      await confirmToolCall(request.request_id, approved);
    } catch (confirmError: unknown) {
      setError(
        confirmError instanceof Error ? confirmError.message : "Could not submit the confirmation.",
      );
    }
  }

  handlers.current = { start: startListening, stop: stopListening, interrupt };

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.code === "Space" && !isTypingTarget(event.target)) {
        event.preventDefault();
        if (!event.repeat) void handlers.current.start();
      } else if (event.code === "Escape") {
        handlers.current.interrupt();
      }
    };
    const onKeyUp = (event: KeyboardEvent): void => {
      if (event.code === "Space") handlers.current.stop();
    };
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      const rec = recorder.current;
      if (rec) {
        rec.onstop = null;
        if (rec.state === "recording") rec.stop();
        rec.stream.getTracks().forEach((track) => track.stop());
      }
      handlers.current.interrupt();
    };
  }, []);

  return (
    <main className="app-shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="Javis home">
          <span className="brand-mark" aria-hidden="true">J</span>
          <span>javis<span className="brand-ai">.ai</span></span>
        </a>
        <div className="header-actions">
          <span className="local-indicator"><span /> Local and private</span>
          <button className="new-chat-button" onClick={onOpenChat}>Chat</button>
        </div>
      </header>

      <section className="voice-stage" aria-label="Voice assistant">
        <button
          className={`voice-orb ${state}`}
          data-voice-orb=""
          aria-label="Hold to talk"
          onPointerDown={() => void startListening()}
          onPointerUp={stopListening}
          onPointerCancel={stopListening}
          onContextMenu={(event) => event.preventDefault()}
        />
        <p className="eyebrow voice-status" aria-live="polite">{STATUS_LABEL[state]}</p>
        {toolName && <p className="voice-tool">Using {toolName}…</p>}
        {heard && <p className="voice-heard">“{heard}”</p>}
        {reply && (
          <div className="voice-reply">
            <ReactMarkdown>{reply}</ReactMarkdown>
          </div>
        )}
        {approval && (
          <div className="voice-approval" role="alertdialog" aria-label="Approval needed">
            <p>Javis wants to run <strong>{approval.name}</strong>.</p>
            <pre>{JSON.stringify(approval.arguments, null, 2)}</pre>
            <div>
              <button className="approval-deny" onClick={() => void respondToApproval(false)}>
                Deny
              </button>
              <button className="approval-allow" onClick={() => void respondToApproval(true)}>
                Approve
              </button>
            </div>
          </div>
        )}
        {error && <p className="error-message" role="alert">{error}</p>}
        <p className="composer-hint">
          Hold Space or the orb to talk <span>·</span> Esc to stop
        </p>
      </section>
    </main>
  );
}
