import React, { useRef, useEffect, useState, useCallback } from "react";
import { Settings, X, ArrowUp, Sparkles, Globe, ArrowRight } from "lucide-react";
import { useChatSession, type ParsedCommand, type Message } from "@/hooks/use-chat-session";
import { useBrowser } from "@/context/BrowserContext";

const MODELS = [
  { id: "deepseek-v3.2", label: "DeepSeek V3.2" },
  { id: "gemini-3-flash-preview", label: "Gemini 3 Flash Preview" },
  { id: "kimi-k2.5", label: "Kimi K2.5" },
];

const SUGGESTIONS = [
  "Me explique IA de forma simples",
  "Escreva um código Python simples",
  "Me ajude a redigir um e-mail profissional",
  "Qual a diferença entre React e Vue?",
];

function formatTime(d: Date) {
  return d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
}

function MessageBubble({ msg, onCommand }: { msg: Message; onCommand: (c: ParsedCommand) => void }) {
  const isUser = msg.role === "user";
  return (
    <div
      style={{
        display: "flex",
        flexDirection: isUser ? "row-reverse" : "row",
        gap: 10,
        marginBottom: 16,
      }}
    >
      {!isUser && (
        <div
          style={{
            width: 30,
            height: 30,
            borderRadius: 8,
            background: "rgba(255,255,255,0.08)",
            border: "1px solid rgba(255,255,255,0.12)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flexShrink: 0,
            marginTop: 2,
          }}
        >
          <Sparkles size={14} color="rgba(255,255,255,0.7)" />
        </div>
      )}
      <div
        style={{
          maxWidth: "80%",
          display: "flex",
          flexDirection: "column",
          alignItems: isUser ? "flex-end" : "flex-start",
          gap: 4,
        }}
      >
        <div
          style={{
            padding: "10px 14px",
            borderRadius: isUser ? "18px 18px 4px 18px" : "18px 18px 18px 4px",
            background: isUser ? "#ffffff" : "rgba(255,255,255,0.06)",
            border: isUser ? "none" : "1px solid rgba(255,255,255,0.1)",
            color: isUser ? "#000000" : "#f0f0f0",
            fontSize: 15,
            lineHeight: 1.5,
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          {msg.content}
        </div>
        {msg.commands && msg.commands.length > 0 && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 2 }}>
            {msg.commands.map((cmd, i) => {
              const label =
                cmd.type === "stop" ? "Parar Browser" :
                cmd.type === "new_task" ? `Executar: "${(cmd.param ?? "").slice(0, 25)}..."` :
                `Responder: "${(cmd.param ?? "").slice(0, 20)}..."`;
              return (
                <button
                  key={i}
                  onClick={() => onCommand(cmd)}
                  style={{
                    padding: "6px 12px",
                    borderRadius: 8,
                    border: "1px solid rgba(255,255,255,0.2)",
                    background: "rgba(255,255,255,0.08)",
                    color: "#e0e0e0",
                    fontSize: 12,
                    cursor: "pointer",
                    fontFamily: "inherit",
                  }}
                >
                  {label}
                </button>
              );
            })}
          </div>
        )}
        <span style={{ fontSize: 10, color: "rgba(255,255,255,0.25)", paddingLeft: 2, paddingRight: 2 }}>
          {formatTime(msg.timestamp)}
        </span>
      </div>
    </div>
  );
}

function TypingIndicator() {
  return (
    <div style={{ display: "flex", gap: 10, marginBottom: 16, alignItems: "center" }}>
      <div
        style={{
          width: 30,
          height: 30,
          borderRadius: 8,
          background: "rgba(255,255,255,0.08)",
          border: "1px solid rgba(255,255,255,0.12)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
        }}
      >
        <Sparkles size={14} color="rgba(255,255,255,0.7)" />
      </div>
      <div
        style={{
          padding: "12px 16px",
          borderRadius: "18px 18px 18px 4px",
          background: "rgba(255,255,255,0.06)",
          border: "1px solid rgba(255,255,255,0.1)",
          display: "flex",
          gap: 4,
          alignItems: "center",
        }}
      >
        {[0, 150, 300].map((delay) => (
          <div
            key={delay}
            style={{
              width: 6,
              height: 6,
              borderRadius: "50%",
              background: "rgba(255,255,255,0.5)",
              animation: `bounce 1s ${delay}ms infinite`,
            }}
          />
        ))}
      </div>
    </div>
  );
}

export default function MobileChatPage({ onSwitchToBrowser }: { onSwitchToBrowser: () => void }) {
  const [input, setInput] = useState("");
  const [showSettings, setShowSettings] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const messagesBottomRef = useRef<HTMLDivElement>(null);
  const { task, isRunning, runTask, stopTask, respondToHuman } = useBrowser();

  const buildBrowserContext = useCallback(() => {
    if (!task) return "";
    return `
Você também controla um browser agent (navegador automático).
Estado atual:
- Status: ${isRunning ? "em execução" : task.status === "completed" ? "concluído" : "falhou"}
- Passo: ${task.currentStep ?? 0}
- URL: ${task.currentUrl ?? "nenhuma"}
- Ação: ${task.currentAction ?? "nenhuma"}
${task.waitingForHuman ? `- AGUARDANDO RESPOSTA: ${task.humanQuestion}` : ""}

Para controlar o browser, inclua na sua resposta:
- Parar: [CMD:stop]
- Nova tarefa: [CMD:new_task:descrição da tarefa] (usa modelo configurado no browser)
- Responder ao agente: [CMD:respond:sua resposta]

Se não entender o pedido, explique o que você pode fazer. Responda no mesmo idioma do usuário.
    `.trim();
  }, [task, isRunning]);

  const handleCommand = useCallback((cmd: ParsedCommand) => {
    if (cmd.type === "stop") stopTask();
    if (cmd.type === "new_task" && cmd.param) runTask(cmd.param, "deepseek-v3.2");
    if (cmd.type === "respond" && cmd.param) respondToHuman(cmd.param);
  }, [stopTask, runTask, respondToHuman]);

  const { messages, model, setModel, systemPrompt, setSystemPrompt, isTyping, sendMessage, clearChat } =
    useChatSession({
      browserContextForPrompt: task ? buildBrowserContext : undefined,
      onCommand: handleCommand,
    });

  useEffect(() => {
    messagesBottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isTyping]);

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = Math.min(textareaRef.current.scrollHeight, 120) + "px";
    }
  }, [input]);

  const send = () => {
    const trimmed = input.trim();
    if (!trimmed || isTyping) return;
    sendMessage(trimmed);
    setInput("");
    if (textareaRef.current) textareaRef.current.style.height = "auto";
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", background: "#0a0a0a" }}>
      {/* Sub-header: model + settings */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "8px 16px",
          borderBottom: "1px solid rgba(255,255,255,0.06)",
          flexShrink: 0,
        }}
      >
        <select
          value={model}
          onChange={(e) => setModel(e.target.value as any)}
          style={{
            background: "rgba(255,255,255,0.06)",
            border: "1px solid rgba(255,255,255,0.1)",
            borderRadius: 8,
            padding: "5px 10px",
            color: "#d0d0d0",
            fontSize: 12,
            fontFamily: "inherit",
            cursor: "pointer",
            outline: "none",
          }}
        >
          {MODELS.map((m) => (
            <option key={m.id} value={m.id} style={{ background: "#1a1a1a" }}>{m.label}</option>
          ))}
        </select>
        <div style={{ display: "flex", gap: 8 }}>
          <button
            onClick={() => setShowSettings((v) => !v)}
            style={{
              background: showSettings ? "rgba(255,255,255,0.1)" : "transparent",
              border: "1px solid rgba(255,255,255,0.1)",
              borderRadius: 8,
              padding: "5px 10px",
              color: "rgba(255,255,255,0.5)",
              fontSize: 12,
              cursor: "pointer",
              fontFamily: "inherit",
            }}
          >
            <Settings size={12} style={{ marginRight: 4 }} /> Config
          </button>
          <button
            onClick={clearChat}
            style={{
              background: "transparent",
              border: "1px solid rgba(255,255,255,0.1)",
              borderRadius: 8,
              padding: "5px 10px",
              color: "rgba(255,255,255,0.4)",
              fontSize: 12,
              cursor: "pointer",
              fontFamily: "inherit",
            }}
          >
            <X size={12} style={{ marginRight: 4 }} /> Limpar
          </button>
        </div>
      </div>

      {/* Settings panel */}
      {showSettings && (
        <div
          style={{
            padding: "12px 16px",
            borderBottom: "1px solid rgba(255,255,255,0.06)",
            flexShrink: 0,
            background: "rgba(255,255,255,0.02)",
          }}
        >
          <p style={{ fontSize: 11, color: "rgba(255,255,255,0.4)", marginBottom: 6, fontWeight: 600, letterSpacing: "0.05em", textTransform: "uppercase" }}>
            Prompt do sistema
          </p>
          <textarea
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            rows={3}
            style={{
              width: "100%",
              background: "rgba(255,255,255,0.05)",
              border: "1px solid rgba(255,255,255,0.12)",
              borderRadius: 10,
              padding: "10px 12px",
              color: "#e0e0e0",
              fontSize: 13,
              fontFamily: "inherit",
              resize: "none",
              outline: "none",
              lineHeight: 1.5,
              boxSizing: "border-box",
            }}
          />
        </div>
      )}

      {/* Browser status banner */}
      {task && (
        <button
          onClick={onSwitchToBrowser}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            margin: "8px 16px 0",
            padding: "8px 12px",
            borderRadius: 10,
            border: "1px solid rgba(255,255,255,0.12)",
            background: "rgba(255,255,255,0.04)",
            color: "rgba(255,255,255,0.55)",
            fontSize: 12,
            cursor: "pointer",
            textAlign: "left",
            flexShrink: 0,
            fontFamily: "inherit",
          }}
        >
          <Globe size={13} style={{ flexShrink: 0 }} />
          <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {isRunning ? (task.currentAction || "Browser em execução...") :
             task.status === "completed" ? "Browser concluído" : "Browser parou"}
          </span>
          <ArrowRight size={13} style={{ flexShrink: 0, opacity: 0.5 }} />
        </button>
      )}

      {/* Messages area */}
      <div
        style={{
          flex: 1,
          overflowY: "auto",
          overflowX: "hidden",
          padding: "16px",
          scrollbarWidth: "none",
        }}
      >
        {messages.length === 0 ? (
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              height: "100%",
              gap: 20,
              paddingBottom: 40,
            }}
          >
            <div
              style={{
                width: 56,
                height: 56,
                borderRadius: 16,
                background: "rgba(255,255,255,0.06)",
                border: "1px solid rgba(255,255,255,0.12)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <Sparkles size={24} color="rgba(255,255,255,0.6)" />
            </div>
            <div style={{ textAlign: "center" }}>
              <p style={{ fontSize: 20, fontWeight: 700, color: "#ffffff", marginBottom: 6 }}>
                Como posso ajudar?
              </p>
              <p style={{ fontSize: 13, color: "rgba(255,255,255,0.4)", maxWidth: 260, lineHeight: 1.5 }}>
                Converse comigo ou peça para eu controlar o browser.
              </p>
            </div>
            <div style={{ width: "100%", display: "flex", flexDirection: "column", gap: 8, maxWidth: 320 }}>
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => sendMessage(s)}
                  style={{
                    padding: "12px 16px",
                    borderRadius: 12,
                    border: "1px solid rgba(255,255,255,0.08)",
                    background: "rgba(255,255,255,0.03)",
                    color: "rgba(255,255,255,0.55)",
                    fontSize: 13,
                    cursor: "pointer",
                    textAlign: "left",
                    fontFamily: "inherit",
                    lineHeight: 1.4,
                  }}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", paddingBottom: 8 }}>
            {messages.map((msg) => (
              <MessageBubble key={msg.id} msg={msg} onCommand={handleCommand} />
            ))}
            {isTyping && <TypingIndicator />}
            <div ref={messagesBottomRef} />
          </div>
        )}
      </div>

      {/* Input area */}
      <div
        style={{
          flexShrink: 0,
          padding: "10px 16px 16px",
          borderTop: "1px solid rgba(255,255,255,0.06)",
          background: "#0a0a0a",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "flex-end",
            gap: 10,
            background: "rgba(255,255,255,0.06)",
            border: "1px solid rgba(255,255,255,0.12)",
            borderRadius: 16,
            padding: "10px 12px",
          }}
        >
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            placeholder="Mensagem..."
            disabled={isTyping}
            rows={1}
            style={{
              flex: 1,
              background: "transparent",
              border: "none",
              outline: "none",
              color: "#f0f0f0",
              fontSize: 15,
              fontFamily: "inherit",
              lineHeight: 1.5,
              resize: "none",
              overflow: "hidden",
              maxHeight: 120,
              padding: 0,
              margin: 0,
              opacity: isTyping ? 0.5 : 1,
            }}
          />
          <button
            onClick={send}
            disabled={!input.trim() || isTyping}
            style={{
              width: 36,
              height: 36,
              borderRadius: 10,
              border: "none",
              background: input.trim() && !isTyping ? "#ffffff" : "rgba(255,255,255,0.1)",
              color: input.trim() && !isTyping ? "#000000" : "rgba(255,255,255,0.3)",
              cursor: input.trim() && !isTyping ? "pointer" : "default",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              flexShrink: 0,
              fontFamily: "inherit",
              transition: "all 0.15s",
            }}
          >
            <ArrowUp size={17} />
          </button>
        </div>
      </div>

      <style>{`
        @keyframes bounce {
          0%, 100% { transform: translateY(0); opacity: 0.4; }
          50% { transform: translateY(-4px); opacity: 1; }
        }
        textarea::-webkit-scrollbar { display: none; }
      `}</style>
    </div>
  );
}
