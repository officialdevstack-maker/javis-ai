import { useState } from "react";
import Chat from "./Chat";
import Sources from "./Sources";
import "./App.css";

export default function App() {
  const [view, setView] = useState<"chat" | "sources">("chat");
  return view === "chat" ? (
    <Chat onOpenSources={() => setView("sources")} />
  ) : (
    <Sources onBack={() => setView("chat")} />
  );
}
