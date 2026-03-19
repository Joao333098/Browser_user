import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MessageSquare, Globe, Loader2 } from "lucide-react";
import { BrowserProvider, useBrowser } from "@/context/BrowserContext";
import MobileChatPage from "@/pages/MobileChatPage";
import MobileBrowserPage from "@/pages/MobileBrowserPage";

const queryClient = new QueryClient();

type Tab = "chat" | "browser";

function TopNav({ active, onSelect }: { active: Tab; onSelect: (t: Tab) => void }) {
  const { isRunning } = useBrowser();

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "12px 20px",
        borderBottom: "1px solid rgba(255,255,255,0.08)",
        background: "#0a0a0a",
        flexShrink: 0,
      }}
    >
      <span style={{ fontWeight: 700, fontSize: 18, color: "#fff", letterSpacing: "-0.3px" }}>
        Nova
      </span>
      <div style={{ display: "flex", gap: 4, background: "rgba(255,255,255,0.06)", borderRadius: 12, padding: 3 }}>
        <button
          onClick={() => onSelect("chat")}
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            width: 40,
            height: 36,
            borderRadius: 9,
            border: "none",
            cursor: "pointer",
            background: active === "chat" ? "rgba(255,255,255,0.12)" : "transparent",
            color: active === "chat" ? "#ffffff" : "rgba(255,255,255,0.35)",
            transition: "all 0.15s ease",
          }}
        >
          <MessageSquare size={18} />
        </button>
        <button
          onClick={() => onSelect("browser")}
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            width: 40,
            height: 36,
            borderRadius: 9,
            border: "none",
            cursor: "pointer",
            position: "relative",
            background: active === "browser" ? "rgba(255,255,255,0.12)" : "transparent",
            color: active === "browser" ? "#ffffff" : "rgba(255,255,255,0.35)",
            transition: "all 0.15s ease",
          }}
        >
          <Globe size={18} />
          {isRunning && (
            <span
              style={{
                position: "absolute",
                top: 5,
                right: 5,
                width: 7,
                height: 7,
                borderRadius: "50%",
                background: "#fff",
                border: "1.5px solid #0a0a0a",
                animation: "pulse 1.5s infinite",
              }}
            />
          )}
        </button>
      </div>
    </div>
  );
}

function AppShell() {
  const [activeTab, setActiveTab] = useState<Tab>("chat");

  return (
    <div style={{ height: "100dvh", display: "flex", flexDirection: "column", background: "#0a0a0a", overflow: "hidden" }}>
      <TopNav active={activeTab} onSelect={setActiveTab} />
      <div style={{ flex: 1, overflow: "hidden", position: "relative" }}>
        <div style={{ display: activeTab === "chat" ? "flex" : "none", flexDirection: "column", height: "100%" }}>
          <MobileChatPage onSwitchToBrowser={() => setActiveTab("browser")} />
        </div>
        <div style={{ display: activeTab === "browser" ? "flex" : "none", flexDirection: "column", height: "100%" }}>
          <MobileBrowserPage />
        </div>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserProvider>
        <AppShell />
      </BrowserProvider>
    </QueryClientProvider>
  );
}
