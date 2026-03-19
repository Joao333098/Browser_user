import { useState, useRef, useEffect } from "react";
import {
  Check, X, Zap, ArrowRight, HelpCircle, Globe, Monitor,
  Brain, Square, Play, ChevronDown, ChevronUp, ZoomIn, Maximize2, Send
} from "lucide-react";
import { useBrowser, type AgentEvent } from "@/context/BrowserContext";

const MODELS = [
  { id: "llama-3.3-70b-versatile", label: "Llama 3.3 70B (padrão)" },
  { id: "llama-3.1-8b-instant", label: "Llama 3.1 8B (rápido)" },
  { id: "meta-llama/llama-4-scout-17b-16e-instruct", label: "Llama 4 Scout (visão)" },
];

const QUICK_EXAMPLES = [
  "Vá para github.com/browser-use/browser-use e me diga o número de stars",
  "Acesse wikipedia.org e resuma o artigo sobre Inteligência Artificial",
  "Pesquise no google.com 'melhores jogos 2025' e liste os 5 primeiros",
];

function formatTime(iso?: string) {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function StepRow({ event }: { event: AgentEvent }) {
  const iconEl =
    event.type === "done" ? <Check size={13} /> :
    event.type === "error" ? <X size={13} /> :
    event.type === "started" ? <Zap size={13} /> :
    event.type === "human_input_required" ? <HelpCircle size={13} /> :
    <ArrowRight size={13} />;

  const iconColor =
    event.type === "done" ? "#6ee7b7" :
    event.type === "error" ? "#fca5a5" :
    event.type === "started" ? "#fde68a" :
    event.type === "human_input_required" ? "#fcd34d" :
    "rgba(255,255,255,0.5)";

  return (
    <div
      style={{
        display: "flex",
        gap: 10,
        padding: "10px 12px",
        borderRadius: 10,
        marginBottom: 6,
        border: "1px solid rgba(255,255,255,0.07)",
        background: event.type === "done" ? "rgba(255,255,255,0.04)" :
                    event.type === "error" ? "rgba(255,100,100,0.05)" :
                    "rgba(255,255,255,0.02)",
      }}
    >
      <span style={{ color: iconColor, fontWeight: 700, flexShrink: 0, marginTop: 2, display: "flex" }}>
        {iconEl}
      </span>
      <div style={{ flex: 1, minWidth: 0 }}>
        {event.type === "step" && (
          <>
            {event.action && (
              <p style={{ fontSize: 13, color: "rgba(255,255,255,0.8)", lineHeight: 1.4, marginBottom: 2 }}>
                {event.action}
              </p>
            )}
            {event.thought && (
              <p style={{ fontSize: 11, color: "rgba(255,255,255,0.3)", fontStyle: "italic", lineHeight: 1.4 }}>
                {event.thought}
              </p>
            )}
            {event.url && (
              <p style={{ fontSize: 10, color: "rgba(255,255,255,0.25)", marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {event.url.replace(/^https?:\/\//, "")}
              </p>
            )}
          </>
        )}
        {event.type === "started" && (
          <p style={{ fontSize: 13, color: "rgba(255,255,255,0.65)" }}>{event.message}</p>
        )}
        {event.type === "human_input_required" && (
          <>
            <p style={{ fontSize: 13, color: "#fcd34d", fontWeight: 600 }}>Aguardando sua resposta</p>
            {event.question && (
              <p style={{ fontSize: 12, color: "rgba(252,211,77,0.65)", marginTop: 2 }}>{event.question}</p>
            )}
          </>
        )}
        {event.type === "done" && (
          <>
            <p style={{ fontSize: 13, color: "#6ee7b7", fontWeight: 600 }}>Concluído!</p>
            {event.result && (
              <p style={{ fontSize: 12, color: "rgba(255,255,255,0.45)", marginTop: 4, whiteSpace: "pre-wrap" }}>
                {event.result}
              </p>
            )}
          </>
        )}
        {event.type === "error" && (
          <p style={{ fontSize: 13, color: "#fca5a5" }}>{event.error}</p>
        )}
      </div>
      <span style={{ fontSize: 10, color: "rgba(255,255,255,0.18)", flexShrink: 0, alignSelf: "flex-start", marginTop: 2 }}>
        {event.step != null ? `#${event.step}` : formatTime(event.timestamp)}
      </span>
    </div>
  );
}

function ScreenshotFullscreen({ src, onClose }: { src: string; onClose: () => void }) {
  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.95)",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 200,
        padding: 16,
      }}
    >
      <button
        onClick={onClose}
        style={{
          position: "absolute",
          top: 16,
          right: 16,
          width: 36,
          height: 36,
          borderRadius: 10,
          border: "1px solid rgba(255,255,255,0.2)",
          background: "rgba(255,255,255,0.08)",
          color: "#ffffff",
          cursor: "pointer",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <X size={18} />
      </button>
      <img
        src={`data:image/jpeg;base64,${src}`}
        alt="Browser"
        style={{
          maxWidth: "100%",
          maxHeight: "calc(100vh - 80px)",
          objectFit: "contain",
          borderRadius: 12,
          border: "1px solid rgba(255,255,255,0.12)",
        }}
        onClick={(e) => e.stopPropagation()}
      />
      <p style={{ color: "rgba(255,255,255,0.3)", fontSize: 12, marginTop: 12 }}>
        Toque fora para fechar
      </p>
    </div>
  );
}

function HumanInputModal({
  question,
  screenshot,
  onSubmit,
}: {
  question: string;
  screenshot?: string;
  onSubmit: (r: string) => void;
}) {
  const [value, setValue] = useState("");
  const [showFullscreen, setShowFullscreen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setTimeout(() => inputRef.current?.focus(), 150);
  }, []);

  return (
    <>
      <div
        style={{
          position: "absolute",
          inset: 0,
          background: "rgba(0,0,0,0.85)",
          display: "flex",
          flexDirection: "column",
          justifyContent: "flex-end",
          zIndex: 100,
        }}
      >
        <div
          style={{
            background: "#141414",
            borderTop: "1px solid rgba(252,211,77,0.3)",
            borderRadius: "20px 20px 0 0",
            padding: "20px 20px 32px",
            maxHeight: "85vh",
            overflowY: "auto",
          }}
        >
          <div style={{ display: "flex", gap: 10, marginBottom: 16 }}>
            <div
              style={{
                width: 36,
                height: 36,
                borderRadius: 10,
                background: "rgba(252,211,77,0.1)",
                border: "1px solid rgba(252,211,77,0.25)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                flexShrink: 0,
              }}
            >
              <HelpCircle size={18} color="#fcd34d" />
            </div>
            <div>
              <p style={{ fontSize: 13, fontWeight: 600, color: "#fcd34d", marginBottom: 4 }}>
                O agente precisa da sua ajuda
              </p>
              <p style={{ fontSize: 12, color: "rgba(252,211,77,0.6)", lineHeight: 1.5 }}>{question}</p>
            </div>
          </div>

          {screenshot && (
            <div style={{ marginBottom: 16 }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
                <p style={{ fontSize: 11, color: "rgba(255,255,255,0.35)", fontWeight: 600, letterSpacing: "0.05em", textTransform: "uppercase" }}>
                  Visualização do browser
                </p>
                <button
                  onClick={() => setShowFullscreen(true)}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 4,
                    padding: "4px 8px",
                    borderRadius: 6,
                    border: "1px solid rgba(252,211,77,0.3)",
                    background: "rgba(252,211,77,0.08)",
                    color: "#fcd34d",
                    fontSize: 11,
                    cursor: "pointer",
                    fontFamily: "inherit",
                  }}
                >
                  <Maximize2 size={11} />
                  Ampliar
                </button>
              </div>
              <div
                onClick={() => setShowFullscreen(true)}
                style={{
                  borderRadius: 12,
                  overflow: "hidden",
                  border: "2px solid rgba(252,211,77,0.25)",
                  background: "#111",
                  cursor: "zoom-in",
                  position: "relative",
                }}
              >
                <img
                  src={`data:image/jpeg;base64,${screenshot}`}
                  alt="CAPTCHA / Browser"
                  style={{ width: "100%", display: "block" }}
                />
                <div
                  style={{
                    position: "absolute",
                    bottom: 8,
                    right: 8,
                    background: "rgba(0,0,0,0.6)",
                    borderRadius: 6,
                    padding: "4px 6px",
                    display: "flex",
                    alignItems: "center",
                    gap: 4,
                  }}
                >
                  <ZoomIn size={12} color="rgba(255,255,255,0.7)" />
                  <span style={{ fontSize: 10, color: "rgba(255,255,255,0.7)" }}>Toque para ampliar</span>
                </div>
              </div>
            </div>
          )}

          <div style={{ display: "flex", gap: 8 }}>
            <input
              ref={inputRef}
              type="text"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && value.trim()) onSubmit(value.trim()); }}
              placeholder="Sua resposta..."
              style={{
                flex: 1,
                background: "rgba(255,255,255,0.06)",
                border: "1px solid rgba(252,211,77,0.3)",
                borderRadius: 12,
                padding: "12px 14px",
                color: "#f0f0f0",
                fontSize: 15,
                fontFamily: "inherit",
                outline: "none",
              }}
            />
            <button
              onClick={() => value.trim() && onSubmit(value.trim())}
              disabled={!value.trim()}
              style={{
                padding: "12px 16px",
                borderRadius: 12,
                border: "1px solid rgba(252,211,77,0.25)",
                background: "rgba(252,211,77,0.12)",
                color: "#fcd34d",
                cursor: value.trim() ? "pointer" : "default",
                opacity: value.trim() ? 1 : 0.4,
                fontFamily: "inherit",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <ArrowRight size={18} />
            </button>
          </div>
        </div>
      </div>

      {showFullscreen && screenshot && (
        <ScreenshotFullscreen src={screenshot} onClose={() => setShowFullscreen(false)} />
      )}
    </>
  );
}

function TaskSheet({
  onClose,
  onRun,
}: {
  onClose: () => void;
  onRun: (task: string, model: string) => void;
}) {
  const [taskText, setTaskText] = useState("");
  const [model, setModel] = useState("llama-3.3-70b-versatile");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    setTimeout(() => textareaRef.current?.focus(), 150);
  }, []);

  const handleRun = () => {
    if (taskText.trim()) {
      onRun(taskText.trim(), model);
      onClose();
    }
  };

  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        background: "rgba(0,0,0,0.75)",
        display: "flex",
        flexDirection: "column",
        justifyContent: "flex-end",
        zIndex: 100,
      }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div
        style={{
          background: "#141414",
          borderTop: "1px solid rgba(255,255,255,0.1)",
          borderRadius: "20px 20px 0 0",
          padding: "16px 20px 32px",
        }}
      >
        <div style={{ width: 36, height: 4, borderRadius: 2, background: "rgba(255,255,255,0.2)", margin: "0 auto 16px" }} />

        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
          <p style={{ fontSize: 16, fontWeight: 700, color: "#ffffff" }}>Nova tarefa</p>
          <button
            onClick={onClose}
            style={{
              width: 30,
              height: 30,
              borderRadius: 8,
              border: "1px solid rgba(255,255,255,0.1)",
              background: "rgba(255,255,255,0.05)",
              color: "rgba(255,255,255,0.5)",
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontFamily: "inherit",
            }}
          >
            <X size={14} />
          </button>
        </div>

        <div style={{ marginBottom: 12 }}>
          <p style={{ fontSize: 11, color: "rgba(255,255,255,0.4)", marginBottom: 6, fontWeight: 600, letterSpacing: "0.05em", textTransform: "uppercase" }}>
            Modelo
          </p>
          <select
            value={model}
            onChange={(e) => setModel(e.target.value)}
            style={{
              width: "100%",
              background: "rgba(255,255,255,0.06)",
              border: "1px solid rgba(255,255,255,0.12)",
              borderRadius: 10,
              padding: "10px 12px",
              color: "#d0d0d0",
              fontSize: 14,
              fontFamily: "inherit",
              cursor: "pointer",
              outline: "none",
              appearance: "auto",
            }}
          >
            {MODELS.map((m) => (
              <option key={m.id} value={m.id} style={{ background: "#1a1a1a" }}>{m.label}</option>
            ))}
          </select>
        </div>

        <div style={{ marginBottom: 12 }}>
          <p style={{ fontSize: 11, color: "rgba(255,255,255,0.4)", marginBottom: 6, fontWeight: 600, letterSpacing: "0.05em", textTransform: "uppercase" }}>
            Descrição da tarefa
          </p>
          <textarea
            ref={textareaRef}
            value={taskText}
            onChange={(e) => setTaskText(e.target.value)}
            placeholder="Descreva o que o agente deve fazer no browser..."
            rows={4}
            style={{
              width: "100%",
              background: "rgba(255,255,255,0.05)",
              border: "1px solid rgba(255,255,255,0.12)",
              borderRadius: 10,
              padding: "12px 14px",
              color: "#f0f0f0",
              fontSize: 15,
              fontFamily: "inherit",
              resize: "none",
              outline: "none",
              lineHeight: 1.5,
              boxSizing: "border-box",
            }}
          />
        </div>

        <div style={{ marginBottom: 14 }}>
          <p style={{ fontSize: 11, color: "rgba(255,255,255,0.3)", marginBottom: 6, fontWeight: 600, letterSpacing: "0.05em", textTransform: "uppercase" }}>
            Exemplos
          </p>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {QUICK_EXAMPLES.map((ex) => (
              <button
                key={ex}
                onClick={() => setTaskText(ex)}
                style={{
                  padding: "8px 12px",
                  borderRadius: 8,
                  border: "1px solid rgba(255,255,255,0.07)",
                  background: "rgba(255,255,255,0.02)",
                  color: "rgba(255,255,255,0.4)",
                  fontSize: 12,
                  cursor: "pointer",
                  textAlign: "left",
                  fontFamily: "inherit",
                  lineHeight: 1.4,
                }}
              >
                {ex}
              </button>
            ))}
          </div>
        </div>

        <button
          onClick={handleRun}
          disabled={!taskText.trim()}
          style={{
            width: "100%",
            padding: "14px",
            borderRadius: 12,
            border: "none",
            background: taskText.trim() ? "#ffffff" : "rgba(255,255,255,0.1)",
            color: taskText.trim() ? "#000000" : "rgba(255,255,255,0.3)",
            fontSize: 15,
            fontWeight: 700,
            cursor: taskText.trim() ? "pointer" : "default",
            fontFamily: "inherit",
            letterSpacing: "-0.2px",
            transition: "all 0.15s",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 8,
          }}
        >
          <Play size={16} />
          Executar tarefa
        </button>
      </div>
    </div>
  );
}

export default function MobileBrowserPage() {
  const { task, isRunning, runTask, stopTask, respondToHuman, injectMessage } = useBrowser();
  const [showTaskSheet, setShowTaskSheet] = useState(false);
  const [showLog, setShowLog] = useState(true);
  const [showFullscreenShot, setShowFullscreenShot] = useState(false);
  const [injectText, setInjectText] = useState("");
  const [injecting, setInjecting] = useState(false);
  const logsEndRef = useRef<HTMLDivElement>(null);
  const injectInputRef = useRef<HTMLInputElement>(null);

  const visibleEvents = task?.events.filter(
    (e) => e.type !== "ping" && e.type !== "connected"
  ) ?? [];

  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [visibleEvents.length]);

  const handleInject = async () => {
    const msg = injectText.trim();
    if (!msg || injecting) return;
    setInjecting(true);
    setInjectText("");
    await injectMessage(msg);
    setInjecting(false);
    injectInputRef.current?.focus();
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", background: "#0a0a0a", position: "relative" }}>
      {/* Status bar */}
      <div
        style={{
          padding: "8px 16px",
          borderBottom: "1px solid rgba(255,255,255,0.06)",
          flexShrink: 0,
          display: "flex",
          alignItems: "center",
          gap: 8,
        }}
      >
        {isRunning ? (
          <>
            <span style={{ width: 8, height: 8, borderRadius: "50%", background: "#ffffff", flexShrink: 0, animation: "pulse 1.5s infinite" }} />
            <span style={{ fontSize: 12, color: "rgba(255,255,255,0.5)", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {task?.currentAction || "Iniciando agente..."}
            </span>
            <span style={{ fontSize: 11, color: "rgba(255,255,255,0.25)", flexShrink: 0 }}>
              Passo {task?.currentStep ?? 0}
            </span>
          </>
        ) : task?.status === "completed" ? (
          <span style={{ fontSize: 12, color: "rgba(110,231,183,0.7)", display: "flex", alignItems: "center", gap: 5 }}>
            <Check size={12} /> Concluído
          </span>
        ) : task?.status === "failed" ? (
          <span style={{ fontSize: 12, color: "rgba(252,165,165,0.7)", display: "flex", alignItems: "center", gap: 5 }}>
            <X size={12} /> Falhou
          </span>
        ) : (
          <span style={{ fontSize: 12, color: "rgba(255,255,255,0.2)" }}>Nenhuma tarefa ativa</span>
        )}
        {task?.currentUrl && isRunning && (
          <span style={{ fontSize: 10, color: "rgba(255,255,255,0.2)", flexShrink: 0, maxWidth: 120, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {task.currentUrl.replace(/^https?:\/\//, "").split("/")[0]}
          </span>
        )}
      </div>

      {/* Live browser view */}
      <div
        onClick={() => task?.latestScreenshot && setShowFullscreenShot(true)}
        style={{
          margin: "12px 16px",
          borderRadius: 12,
          overflow: "hidden",
          border: "1px solid rgba(255,255,255,0.08)",
          background: "#111",
          flexShrink: 0,
          aspectRatio: "16/9",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          cursor: task?.latestScreenshot ? "zoom-in" : "default",
          position: "relative",
        }}
      >
        {task?.latestScreenshot ? (
          <>
            <img
              src={`data:image/jpeg;base64,${task.latestScreenshot}`}
              alt="Browser ao vivo"
              style={{ width: "100%", height: "100%", objectFit: "contain" }}
            />
            {isRunning && (
              <div style={{
                position: "absolute",
                top: 6,
                left: 6,
                background: "rgba(0,0,0,0.6)",
                borderRadius: 6,
                padding: "3px 7px",
                display: "flex",
                alignItems: "center",
                gap: 4,
              }}>
                <span style={{ width: 6, height: 6, borderRadius: "50%", background: "#ef4444", animation: "pulse 1s infinite" }} />
                <span style={{ fontSize: 10, color: "rgba(255,255,255,0.8)", fontWeight: 600 }}>AO VIVO</span>
              </div>
            )}
            <div
              style={{
                position: "absolute",
                bottom: 6,
                right: 6,
                background: "rgba(0,0,0,0.55)",
                borderRadius: 6,
                padding: "3px 6px",
                display: "flex",
                alignItems: "center",
                gap: 4,
              }}
            >
              <ZoomIn size={11} color="rgba(255,255,255,0.6)" />
              <span style={{ fontSize: 10, color: "rgba(255,255,255,0.6)" }}>Ampliar</span>
            </div>
          </>
        ) : (
          <div style={{ textAlign: "center", color: "rgba(255,255,255,0.15)" }}>
            <Monitor size={32} style={{ marginBottom: 8, opacity: 0.4 }} />
            <p style={{ fontSize: 12 }}>Sem visualização</p>
          </div>
        )}
      </div>

      {/* Current thought */}
      {task?.currentThought && isRunning && (
        <div
          style={{
            marginLeft: 16,
            marginRight: 16,
            marginBottom: 8,
            padding: "8px 12px",
            borderRadius: 8,
            border: "1px solid rgba(255,255,255,0.07)",
            background: "rgba(255,255,255,0.03)",
            flexShrink: 0,
            display: "flex",
            gap: 6,
            alignItems: "flex-start",
          }}
        >
          <Brain size={12} color="rgba(255,255,255,0.3)" style={{ marginTop: 2, flexShrink: 0 }} />
          <p style={{ fontSize: 11, color: "rgba(255,255,255,0.35)", fontStyle: "italic", lineHeight: 1.4 }}>
            {task.currentThought}
          </p>
        </div>
      )}

      {/* Steps log */}
      {task && visibleEvents.length > 0 && (
        <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0, marginLeft: 16, marginRight: 16 }}>
          <button
            onClick={() => setShowLog((v) => !v)}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              padding: "6px 10px",
              borderRadius: 8,
              border: "1px solid rgba(255,255,255,0.07)",
              background: "rgba(255,255,255,0.03)",
              color: "rgba(255,255,255,0.35)",
              fontSize: 11,
              cursor: "pointer",
              fontFamily: "inherit",
              marginBottom: 8,
              flexShrink: 0,
              fontWeight: 600,
              letterSpacing: "0.04em",
              textTransform: "uppercase",
            }}
          >
            <span>Log de passos ({visibleEvents.length})</span>
            <span style={{ marginLeft: "auto", display: "flex" }}>
              {showLog ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
            </span>
          </button>
          {showLog && (
            <div style={{ flex: 1, overflowY: "auto", scrollbarWidth: "none", paddingBottom: 8 }}>
              {visibleEvents.map((event, i) => (
                <StepRow key={i} event={event} />
              ))}
              <div ref={logsEndRef} />
            </div>
          )}
        </div>
      )}

      {/* Empty state */}
      {!task && (
        <div
          style={{
            flex: 1,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            gap: 12,
            paddingBottom: 20,
          }}
        >
          <Globe size={36} color="rgba(255,255,255,0.15)" />
          <p style={{ fontSize: 14, color: "rgba(255,255,255,0.3)", textAlign: "center", maxWidth: 240, lineHeight: 1.5 }}>
            Nenhuma tarefa. Toque em "Iniciar Tarefa" para o agente navegar na web.
          </p>
        </div>
      )}

      {/* Bottom action bar */}
      <div
        style={{
          flexShrink: 0,
          padding: "10px 16px 20px",
          borderTop: "1px solid rgba(255,255,255,0.06)",
          background: "#0a0a0a",
        }}
      >
        {isRunning ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {/* Inject instruction input */}
            <div style={{ display: "flex", gap: 8 }}>
              <input
                ref={injectInputRef}
                type="text"
                value={injectText}
                onChange={(e) => setInjectText(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") handleInject(); }}
                placeholder="Adicionar instrução enquanto roda..."
                style={{
                  flex: 1,
                  background: "rgba(255,255,255,0.06)",
                  border: "1px solid rgba(255,255,255,0.12)",
                  borderRadius: 12,
                  padding: "11px 14px",
                  color: "#f0f0f0",
                  fontSize: 14,
                  fontFamily: "inherit",
                  outline: "none",
                }}
              />
              <button
                onClick={handleInject}
                disabled={!injectText.trim() || injecting}
                style={{
                  width: 44,
                  height: 44,
                  borderRadius: 12,
                  border: "1px solid rgba(255,255,255,0.15)",
                  background: injectText.trim() ? "rgba(255,255,255,0.12)" : "rgba(255,255,255,0.04)",
                  color: injectText.trim() ? "#ffffff" : "rgba(255,255,255,0.25)",
                  cursor: injectText.trim() ? "pointer" : "default",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  flexShrink: 0,
                  fontFamily: "inherit",
                  transition: "all 0.15s",
                }}
              >
                <Send size={16} />
              </button>
            </div>
            {/* Stop button */}
            <button
              onClick={stopTask}
              style={{
                width: "100%",
                padding: "11px",
                borderRadius: 12,
                border: "1px solid rgba(255,80,80,0.25)",
                background: "rgba(255,80,80,0.08)",
                color: "rgba(255,140,140,0.9)",
                fontSize: 14,
                fontWeight: 600,
                cursor: "pointer",
                fontFamily: "inherit",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                gap: 8,
              }}
            >
              <Square size={13} /> Parar tarefa
            </button>
          </div>
        ) : (
          <button
            onClick={() => setShowTaskSheet(true)}
            style={{
              width: "100%",
              padding: "14px",
              borderRadius: 12,
              border: "none",
              background: "#ffffff",
              color: "#000000",
              fontSize: 15,
              fontWeight: 700,
              cursor: "pointer",
              fontFamily: "inherit",
              letterSpacing: "-0.2px",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: 8,
            }}
          >
            <Play size={16} /> {task ? "Nova tarefa" : "Iniciar tarefa"}
          </button>
        )}
      </div>

      {showTaskSheet && (
        <TaskSheet
          onClose={() => setShowTaskSheet(false)}
          onRun={(t, m) => runTask(t, m)}
        />
      )}

      {task?.waitingForHuman && task.humanQuestion && (
        <HumanInputModal
          question={task.humanQuestion}
          screenshot={task.latestScreenshot}
          onSubmit={(r) => respondToHuman(r)}
        />
      )}

      {showFullscreenShot && task?.latestScreenshot && (
        <ScreenshotFullscreen
          src={task.latestScreenshot}
          onClose={() => setShowFullscreenShot(false)}
        />
      )}

      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.4; }
        }
      `}</style>
    </div>
  );
}
