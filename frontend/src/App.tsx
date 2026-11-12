import { useState } from "react";
import Chat from "./Chat";
import Safety from "./Safety";
import Sources from "./Sources";
import Voice from "./Voice";
import "./App.css";

type View = "voice" | "chat" | "sources" | "safety";

export default function App() {
  const [view, setView] = useState<View>("voice");
  if (view === "voice") return <Voice onOpenChat={() => setView("chat")} />;
  if (view === "sources") return <Sources onBack={() => setView("chat")} />;
  if (view === "safety") return <Safety onBack={() => setView("chat")} />;
  return (
    <Chat
      onOpenSources={() => setView("sources")}
      onOpenVoice={() => setView("voice")}
      onOpenSafety={() => setView("safety")}
    />
  );
}
