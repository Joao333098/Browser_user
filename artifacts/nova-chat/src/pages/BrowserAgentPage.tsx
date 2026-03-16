import { useState, useRef, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Globe, Play, StopCircle, Loader2, CheckCircle2, XCircle, ChevronDown, ChevronUp, Terminal, ImageIcon } from "lucide-react";

type EventType = "connected" | "started" | "step" | "done" | "error" | "ping" | "stream_end";

interface AgentEvent {
  type: EventType;
  step?: number;
  thought?: string;
  action?: string;
  url?: string;
  screenshot?: string;
  result?: string;
  error?: string;
  message?: string;
  timestamp?: string;
}

interface Task {
  id: string;
  status: "running" | "completed" | "failed";
  events: AgentEvent[];
  latestScreenshot?: string;
}

const MODELS = [
  { id: "nova-2-lite-v1", label: "Nova 2 Lite", desc: "Rápido e balanceado" },
  { id: "nova-pro-v1", label: "Nova Pro", desc: "Máxima capacidade" },
  { id: "nova-lite-v1", label: "Nova Lite", desc: "Leve e veloz" },
];

const EXAMPLE_TASKS = [
  "Acesse google.com e pesquise por 'inteligência artificial 2025'",
  "Vá para github.com/browser-use/browser-use e me diga o número de stars",
  "Acesse wikipedia.org e me resuma o artigo sobre Python (linguagem)",
];

export default function BrowserAgentPage() {
  const [taskInput, setTaskInput] = useState("");
  const [model, setModel] = useState("nova-2-lite-v1");
  const [currentTask, setCurrentTask] = useState<Task | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [showScreenshot, setShowScreenshot] = useState(true);
  const [expandedSteps, setExpandedSteps] = useState<Set<number>>(new Set());
  const logsEndRef = useRef<HTMLDivElement>(null);
  const eventSourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [currentTask?.events]);

  useEffect(() => {
    return () => {
      eventSourceRef.current?.close();
    };
  }, []);

  const stopTask = () => {
    eventSourceRef.current?.close();
    setIsRunning(false);
    if (currentTask) {
      setCurrentTask((t) => t ? { ...t, status: "failed" } : t);
    }
  };

  const runTask = async () => {
    if (!taskInput.trim() || isRunning) return;

    eventSourceRef.current?.close();
    setIsRunning(true);
    setCurrentTask(null);
    setExpandedSteps(new Set());

    try {
      const res = await fetch("/api/browser/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task: taskInput.trim(), model }),
      });

      if (!res.ok) {
        const err = await res.json();
        setCurrentTask({
          id: "err",
          status: "failed",
          events: [{ type: "error", error: err.message || "Falha ao iniciar tarefa" }],
        });
        setIsRunning(false);
        return;
      }

      const { task_id } = await res.json();
      const task: Task = { id: task_id, status: "running", events: [] };
      setCurrentTask(task);

      const es = new EventSource(`/api/browser/stream/${task_id}`);
      eventSourceRef.current = es;

      es.onmessage = (e) => {
        const event: AgentEvent = JSON.parse(e.data);
        setCurrentTask((prev) => {
          if (!prev) return prev;
          const events = [...prev.events, event];
          let status = prev.status;
          let latestScreenshot = prev.latestScreenshot;
          if (event.type === "done") status = "completed";
          if (event.type === "error") status = "failed";
          if (event.screenshot) latestScreenshot = event.screenshot;
          return { ...prev, events, status, latestScreenshot };
        });
        if (event.type === "done" || event.type === "error" || event.type === "stream_end") {
          es.close();
          setIsRunning(false);
        }
      };

      es.onerror = () => {
        es.close();
        setIsRunning(false);
        setCurrentTask((prev) =>
          prev ? { ...prev, status: "failed" } : prev
        );
      };
    } catch (err) {
      setCurrentTask({
        id: "err",
        status: "failed",
        events: [{ type: "error", error: "Não foi possível conectar ao servidor de agentes. Aguarde e tente novamente." }],
      });
      setIsRunning(false);
    }
  };

  const toggleStep = (step: number) => {
    setExpandedSteps((prev) => {
      const next = new Set(prev);
      next.has(step) ? next.delete(step) : next.add(step);
      return next;
    });
  };

  const getStatusIcon = () => {
    if (isRunning) return <Loader2 className="w-4 h-4 animate-spin text-blue-400" />;
    if (currentTask?.status === "completed") return <CheckCircle2 className="w-4 h-4 text-green-400" />;
    if (currentTask?.status === "failed") return <XCircle className="w-4 h-4 text-red-400" />;
    return null;
  };

  const stepEvents = currentTask?.events.filter((e) => e.type === "step") ?? [];
  const doneEvent = currentTask?.events.find((e) => e.type === "done");
  const errorEvent = currentTask?.events.find((e) => e.type === "error");
  const startedEvent = currentTask?.events.find((e) => e.type === "started");

  return (
    <div className="flex flex-col h-screen bg-[#0d0d0f] text-white overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-3 px-5 py-3 border-b border-white/10 shrink-0">
        <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-violet-500 to-blue-600 flex items-center justify-center">
          <Globe className="w-4 h-4 text-white" />
        </div>
        <div>
          <h1 className="text-sm font-semibold text-white">Browser Agent</h1>
          <p className="text-xs text-white/40">Nova AI controla o navegador por você</p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          {getStatusIcon()}
          {currentTask && (
            <span className={`text-xs px-2 py-0.5 rounded-full ${
              currentTask.status === "completed" ? "bg-green-500/15 text-green-400" :
              currentTask.status === "failed" ? "bg-red-500/15 text-red-400" :
              "bg-blue-500/15 text-blue-400"
            }`}>
              {currentTask.status === "completed" ? "Concluído" :
               currentTask.status === "failed" ? "Falhou" : "Executando"}
            </span>
          )}
        </div>
      </div>

      <div className="flex flex-1 min-h-0">
        {/* Left panel - logs */}
        <div className="flex flex-col flex-1 min-w-0 border-r border-white/10">
          {/* Logs area */}
          <div className="flex-1 overflow-y-auto p-4 space-y-2 font-mono text-xs">
            {!currentTask && (
              <div className="flex flex-col items-center justify-center h-full gap-4 text-white/30">
                <Globe className="w-12 h-12" />
                <p>Dê uma tarefa ao agente para começar</p>
                <div className="flex flex-col gap-2 w-full max-w-md">
                  {EXAMPLE_TASKS.map((t) => (
                    <button
                      key={t}
                      onClick={() => setTaskInput(t)}
                      className="text-left px-3 py-2 rounded-lg border border-white/10 hover:border-violet-500/50 hover:bg-violet-500/5 text-white/50 hover:text-white/80 transition-all text-xs"
                    >
                      {t}
                    </button>
                  ))}
                </div>
              </div>
            )}

            <AnimatePresence initial={false}>
              {startedEvent && (
                <motion.div
                  initial={{ opacity: 0, y: 4 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="flex items-center gap-2 text-blue-400"
                >
                  <Terminal className="w-3 h-3 shrink-0" />
                  <span>{startedEvent.message}</span>
                </motion.div>
              )}

              {stepEvents.map((event, i) => (
                <motion.div
                  key={i}
                  initial={{ opacity: 0, y: 4 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="border border-white/8 rounded-lg overflow-hidden"
                >
                  <button
                    onClick={() => toggleStep(event.step ?? i)}
                    className="w-full flex items-center gap-2 px-3 py-2 hover:bg-white/5 transition-colors text-left"
                  >
                    <span className="text-white/30 w-12 shrink-0">Step {event.step}</span>
                    <span className="text-white/70 flex-1 truncate">
                      {event.action || event.thought || "processando..."}
                    </span>
                    {event.url && (
                      <span className="text-violet-400 truncate max-w-[140px] shrink-0">
                        {event.url}
                      </span>
                    )}
                    {expandedSteps.has(event.step ?? i)
                      ? <ChevronUp className="w-3 h-3 text-white/30 shrink-0" />
                      : <ChevronDown className="w-3 h-3 text-white/30 shrink-0" />
                    }
                  </button>
                  <AnimatePresence>
                    {expandedSteps.has(event.step ?? i) && (
                      <motion.div
                        initial={{ height: 0 }}
                        animate={{ height: "auto" }}
                        exit={{ height: 0 }}
                        className="overflow-hidden"
                      >
                        <div className="px-3 pb-3 space-y-1 border-t border-white/8 pt-2">
                          {event.thought && (
                            <div>
                              <span className="text-white/30">💭 Pensamento: </span>
                              <span className="text-white/60">{event.thought}</span>
                            </div>
                          )}
                          {event.action && (
                            <div>
                              <span className="text-white/30">⚡ Ação: </span>
                              <span className="text-white/70">{event.action}</span>
                            </div>
                          )}
                          {event.url && (
                            <div>
                              <span className="text-white/30">🌐 URL: </span>
                              <span className="text-violet-400">{event.url}</span>
                            </div>
                          )}
                        </div>
                      </motion.div>
                    )}
                  </AnimatePresence>
                </motion.div>
              ))}

              {doneEvent && (
                <motion.div
                  initial={{ opacity: 0, y: 4 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="rounded-lg border border-green-500/30 bg-green-500/5 p-3"
                >
                  <div className="flex items-center gap-2 text-green-400 mb-1">
                    <CheckCircle2 className="w-3.5 h-3.5" />
                    <span className="font-medium">Tarefa concluída em {doneEvent.steps} passos</span>
                  </div>
                  {doneEvent.result && (
                    <p className="text-white/70 whitespace-pre-wrap leading-relaxed">
                      {doneEvent.result}
                    </p>
                  )}
                </motion.div>
              )}

              {errorEvent && (
                <motion.div
                  initial={{ opacity: 0, y: 4 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="rounded-lg border border-red-500/30 bg-red-500/5 p-3"
                >
                  <div className="flex items-center gap-2 text-red-400 mb-1">
                    <XCircle className="w-3.5 h-3.5" />
                    <span className="font-medium">Erro</span>
                  </div>
                  <p className="text-white/60">{errorEvent.error}</p>
                </motion.div>
              )}
            </AnimatePresence>

            <div ref={logsEndRef} />
          </div>

          {/* Input */}
          <div className="p-4 border-t border-white/10 space-y-3 shrink-0">
            <div className="flex gap-2">
              {MODELS.map((m) => (
                <button
                  key={m.id}
                  onClick={() => setModel(m.id)}
                  disabled={isRunning}
                  className={`flex-1 text-center px-2 py-1.5 rounded-lg text-xs transition-all border ${
                    model === m.id
                      ? "border-violet-500/70 bg-violet-500/15 text-violet-300"
                      : "border-white/10 text-white/40 hover:border-white/20"
                  }`}
                >
                  {m.label}
                </button>
              ))}
            </div>
            <div className="flex gap-2">
              <textarea
                value={taskInput}
                onChange={(e) => setTaskInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    runTask();
                  }
                }}
                disabled={isRunning}
                placeholder="Descreva a tarefa para o agente... (Enter para enviar)"
                rows={2}
                className="flex-1 bg-white/5 border border-white/10 rounded-xl px-3 py-2.5 text-sm text-white placeholder-white/25 resize-none focus:outline-none focus:border-violet-500/50 disabled:opacity-50"
              />
              <div className="flex flex-col gap-1.5">
                <button
                  onClick={isRunning ? stopTask : runTask}
                  disabled={!taskInput.trim() && !isRunning}
                  className={`px-3 py-2 rounded-xl text-sm font-medium transition-all flex items-center gap-1.5 ${
                    isRunning
                      ? "bg-red-500/20 border border-red-500/40 text-red-400 hover:bg-red-500/30"
                      : "bg-violet-600 hover:bg-violet-500 text-white disabled:opacity-30"
                  }`}
                >
                  {isRunning ? (
                    <><StopCircle className="w-4 h-4" /> Parar</>
                  ) : (
                    <><Play className="w-4 h-4" /> Executar</>
                  )}
                </button>
              </div>
            </div>
          </div>
        </div>

        {/* Right panel - screenshot */}
        <div className="w-80 shrink-0 flex flex-col">
          <div className="flex items-center justify-between px-4 py-3 border-b border-white/10">
            <div className="flex items-center gap-2 text-xs text-white/50">
              <ImageIcon className="w-3.5 h-3.5" />
              <span>Visualização ao vivo</span>
            </div>
            <button
              onClick={() => setShowScreenshot((v) => !v)}
              className="text-xs text-white/30 hover:text-white/60 transition-colors"
            >
              {showScreenshot ? "Ocultar" : "Mostrar"}
            </button>
          </div>
          {showScreenshot && (
            <div className="flex-1 overflow-hidden p-3">
              {currentTask?.latestScreenshot ? (
                <motion.img
                  key={currentTask.latestScreenshot.slice(-20)}
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  src={`data:image/png;base64,${currentTask.latestScreenshot}`}
                  alt="Browser screenshot"
                  className="w-full rounded-lg border border-white/10 object-contain"
                />
              ) : (
                <div className="flex flex-col items-center justify-center h-48 gap-2 text-white/20 border border-white/8 rounded-lg">
                  <Globe className="w-8 h-8" />
                  <span className="text-xs">Screenshot aqui</span>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
