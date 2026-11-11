import { useState } from "react";
import Chat from "./Chat";
import Sources from "./Sources";
import Voice from "./Voice";
import "./App.css";

type View = "voice" | "chat" | "sources";

export default function App() {
  const [view, setView] = useState<View>("voice");
  if (view === "voice") return <Voice onOpenChat={() => setView("chat")} />;
  if (view === "sources") return <Sources onBack={() => setView("chat")} />;
  return (
    <Chat
      onOpenSources={() => setView("sources")}
      onOpenVoice={() => setView("voice")}
    />
  );
}
