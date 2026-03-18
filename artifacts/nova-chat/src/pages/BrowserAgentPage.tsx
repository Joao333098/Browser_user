import { useState, useRef, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Globe, Play, StopCircle, Loader2, CheckCircle2, XCircle,
  Terminal, ImageIcon, ChevronRight, Zap, Copy, Check, Brain,
  MousePointer, Link2, Eye, HelpCircle, Send, SkipForward
} from "lucide-react";

type EventType = "connected" | "started" | "step" | "done" | "error" | "ping" | "stream_end" | "human_input_required";

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
  question?: string;
  timestamp?: string;
}

interface Task {
  id: string;
  status: "running" | "completed" | "failed";
  events: AgentEvent[];
  latestScreenshot?: string;
  currentAction?: string;
  currentThought?: string;
  currentUrl?: string;
  currentStep?: number;
  waitingForHuman?: boolean;
  humanQuestion?: string;
}

const MODELS = [
  { id: "meta-llama/llama-4-scout-17b-16e-instruct", label: "Llama 4 Scout", desc: "Rápido com visão" },
  { id: "meta-llama/llama-4-maverick-17b-128e-instruct", label: "Llama 4 Maverick", desc: "Mais capaz" },
];

const EXAMPLE_TASK = `if has video on quiz pass use the script the console to create a skip video Your first goal is to log in to the website at the following link:
https://clever.com/oauth/authorize?channel=clever-portal&client_id=f86f5e2de0bb792e695c&confirmed=true&district_id=57bc3fbb349cfd010000081c&redirect_uri=https%3A%2F%2Fsso-middleman.sso-prod.il-apps.com%2FClever%2FClever%2FEdgenuity&response_type=code
Use the following credentials to log in:
gmail: 69414@marietta-schools.net
Password: REEF636hulk
Once logged in, your next goal is to complete all available history quizzes, practice exercises, and activities. For each question:
If you know the answer, select it.
If you do not know the answer, search for the correct answer online, review the information, and select the best answer based on your understanding.
If you encounter a video that must be watched before proceeding, attempt to use the browser console to bypass or skip the video so you can continue with the activities.
Your goal is complete when all history quizzes, practices, and activities have been answered correctly and you have reached the end of the assigned tasks or there are no more activities available to complete.`;

const QUICK_EXAMPLES = [
  "Vá para github.com/browser-use/browser-use e me diga o número de stars",
  "Acesse wikipedia.org e me resuma o artigo sobre Inteligência Artificial",
  "Pesquise no google.com 'melhores jogos 2025' e liste os 5 primeiros resultados",
];

function formatTime(iso?: string) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function StepIcon({ type }: { type: EventType }) {
  if (type === "done") return <CheckCircle2 className="w-4 h-4 text-green-400 shrink-0" />;
  if (type === "error") return <XCircle className="w-4 h-4 text-red-400 shrink-0" />;
  if (type === "started") return <Zap className="w-4 h-4 text-yellow-400 shrink-0" />;
  if (type === "human_input_required") return <HelpCircle className="w-4 h-4 text-amber-400 shrink-0" />;
  return <MousePointer className="w-4 h-4 text-blue-400 shrink-0" />;
}

const STORAGE_KEY = "nova_browser_agent";

function saveState(task: Task, taskInput: string, model: string, running: boolean) {
  try {
    const eventsClean = task.events.map(e => ({ ...e, screenshot: undefined }));
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      id: task.id, status: task.status, taskInput, model, running,
      events: eventsClean,
      currentAction: task.currentAction,
      currentThought: task.currentThought,
      currentUrl: task.currentUrl,
      currentStep: task.currentStep,
      waitingForHuman: task.waitingForHuman,
      humanQuestion: task.humanQuestion,
    }));
    if (task.latestScreenshot) {
      try { sessionStorage.setItem(`${STORAGE_KEY}_ss`, task.latestScreenshot); } catch { /* quota */ }
    }
  } catch { /* quota */ }
}

function loadState(): { task: Task; taskInput: string; model: string; running: boolean } | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const d = JSON.parse(raw);
    const latestScreenshot = sessionStorage.getItem(`${STORAGE_KEY}_ss`) ?? undefined;
    return {
      task: {
        id: d.id, status: d.status, events: d.events ?? [],
        latestScreenshot,
        currentAction: d.currentAction, currentThought: d.currentThought,
        currentUrl: d.currentUrl, currentStep: d.currentStep,
        waitingForHuman: d.waitingForHuman, humanQuestion: d.humanQuestion,
      },
      taskInput: d.taskInput ?? "",
      model: d.model ?? "meta-llama/llama-4-scout-17b-16e-instruct",
      running: d.running ?? false,
    };
  } catch { return null; }
}

export default function BrowserAgentPage() {
  const [taskInput, setTaskInput] = useState("");
  const [model, setModel] = useState("meta-llama/llama-4-scout-17b-16e-instruct");
  const [currentTask, setCurrentTask] = useState<Task | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [copied, setCopied] = useState(false);
  const [showExample, setShowExample] = useState(false);
  const [humanInput, setHumanInput] = useState("");
  const [sendingHuman, setSendingHuman] = useState(false);
  const [sessionLimitError, setSessionLimitError] = useState(false);
  const [clearingStuck, setClearingStuck] = useState(false);
  const logsEndRef = useRef<HTMLDivElement>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const humanInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [currentTask?.events]);

  useEffect(() => {
    if (currentTask?.waitingForHuman) humanInputRef.current?.focus();
  }, [currentTask?.waitingForHuman]);

  useEffect(() => () => { eventSourceRef.current?.close(); }, []);

  // Save state to localStorage whenever task or running state changes
  useEffect(() => {
    if (currentTask) saveState(currentTask, taskInput, model, isRunning);
  }, [currentTask, isRunning]);

  const connectToTask = (taskId: string) => {
    eventSourceRef.current?.close();
    const es = new EventSource(`/api/browser/stream/${taskId}`);
    eventSourceRef.current = es;

    es.onmessage = (e) => {
      const event: AgentEvent = JSON.parse(e.data);
      setCurrentTask((prev) => {
        if (!prev) return prev;
        const events = event.type === "ping" ? prev.events : [...prev.events, event];
        let status = prev.status;
        let latestScreenshot = prev.latestScreenshot;
        let currentAction = prev.currentAction;
        let currentThought = prev.currentThought;
        let currentUrl = prev.currentUrl;
        let currentStep = prev.currentStep;
        let waitingForHuman = prev.waitingForHuman;
        let humanQuestion = prev.humanQuestion;

        if (event.screenshot) latestScreenshot = event.screenshot;
        if (event.action) currentAction = event.action;
        if (event.thought) currentThought = event.thought;
        if (event.url) currentUrl = event.url;
        if (event.step != null) currentStep = event.step;

        if (event.type === "human_input_required") {
          waitingForHuman = true;
          humanQuestion = event.question;
          if (event.screenshot) latestScreenshot = event.screenshot;
        }
        if (event.type === "done") {
          status = "completed";
          currentAction = undefined;
          currentThought = undefined;
          waitingForHuman = false;
        }
        if (event.type === "error") {
          status = "failed";
          currentAction = undefined;
          currentThought = undefined;
          waitingForHuman = false;
        }

        return { ...prev, events, status, latestScreenshot, currentAction, currentThought, currentUrl, currentStep, waitingForHuman, humanQuestion };
      });

      if (event.type === "done" || event.type === "error" || event.type === "stream_end") {
        es.close();
        setIsRunning(false);
      }
    };

    es.onerror = () => {
      es.close();
      setIsRunning(false);
      setCurrentTask((prev) => prev ? {
        ...prev,
        status: prev.status === "running" ? "failed" : prev.status,
        waitingForHuman: false,
      } : prev);
    };
  };

  // On mount: restore persisted state and reconnect if task was running
  useEffect(() => {
    const saved = loadState();
    if (!saved) return;
    setTaskInput(saved.taskInput);
    setModel(saved.model);
    setCurrentTask(saved.task);
    if (saved.running && saved.task.status === "running") {
      setIsRunning(true);
      connectToTask(saved.task.id);
    }
  }, []);

  const stopTask = () => {
    eventSourceRef.current?.close();
    setIsRunning(false);
    setCurrentTask((t) => t ? { ...t, status: "failed", waitingForHuman: false } : t);
  };

  const clearTask = () => {
    eventSourceRef.current?.close();
    localStorage.removeItem(STORAGE_KEY);
    sessionStorage.removeItem(`${STORAGE_KEY}_ss`);
    setCurrentTask(null);
    setIsRunning(false);
    setTaskInput("");
    setHumanInput("");
  };

  const copyExample = () => {
    setTaskInput(EXAMPLE_TASK);
    setCopied(true);
    setShowExample(false);
    setTimeout(() => setCopied(false), 2000);
    textareaRef.current?.focus();
  };

  const sendHumanResponse = async () => {
    if (!currentTask || !humanInput.trim() || sendingHuman) return;
    setSendingHuman(true);
    try {
      await fetch(`/api/browser/tasks/${currentTask.id}/respond`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ response: humanInput.trim() }),
      });
      setCurrentTask((t) => t ? { ...t, waitingForHuman: false, humanQuestion: undefined } : t);
      setHumanInput("");
    } catch {
      // ignore
    } finally {
      setSendingHuman(false);
    }
  };

  const clearStuckSessions = async () => {
    setClearingStuck(true);
    try {
      await fetch("/api/browser/tasks/clear-stuck", { method: "POST" });
      setSessionLimitError(false);
      setCurrentTask(null);
    } catch {
      // ignore
    } finally {
      setClearingStuck(false);
    }
  };

  const runTask = async () => {
    if (!taskInput.trim() || isRunning) return;
    eventSourceRef.current?.close();
    setIsRunning(true);
    setCurrentTask(null);
    setHumanInput("");
    setSessionLimitError(false);

    try {
      const res = await fetch("/api/browser/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task: taskInput.trim(), model }),
      });

      if (!res.ok) {
        const err = await res.json();
        if (res.status === 429) {
          setSessionLimitError(true);
          setCurrentTask({ id: "err", status: "failed", events: [{ type: "error", error: "Limite de sessões atingido. Clique em 'Limpar sessões' para liberar." }] });
        } else {
          setCurrentTask({ id: "err", status: "failed", events: [{ type: "error", error: err.detail || err.message || "Falha ao iniciar tarefa" }] });
        }
        setIsRunning(false);
        return;
      }

      const { task_id } = await res.json();
      const task: Task = { id: task_id, status: "running", events: [] };
      setCurrentTask(task);
      connectToTask(task_id);
    } catch {
      setCurrentTask({ id: "err", status: "failed", events: [{ type: "error", error: "Erro de conexão" }] });
      setIsRunning(false);
    }
  };

  const visibleEvents = currentTask?.events.filter(e => e.type !== "ping" && e.type !== "connected") ?? [];

  return (
    <div className="flex flex-col h-screen bg-[#0a0a0f] text-white overflow-hidden pb-16">
      {/* Header */}
      <div className="flex items-center gap-3 px-5 py-3 border-b border-white/8 shrink-0">
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-violet-500 to-blue-600 flex items-center justify-center">
            <Globe className="w-4 h-4 text-white" />
          </div>
          <span className="font-semibold text-sm">Browser Agent</span>
        </div>
        <span className="text-white/30 text-xs flex-1">Navega e age na web automaticamente</span>
        {currentTask && (
          <button
            onClick={clearTask}
            className="text-xs px-3 py-1 rounded-md bg-white/8 hover:bg-white/15 text-white/60 hover:text-white transition-colors"
          >
            + Nova tarefa
          </button>
        )}
      </div>

      <div className="flex flex-1 overflow-hidden">
        {/* Left panel - input + logs */}
        <div className="flex flex-col flex-1 min-w-0 border-r border-white/8">

          {/* Task input */}
          <div className="p-4 border-b border-white/8 shrink-0 space-y-3">
            <textarea
              ref={textareaRef}
              value={taskInput}
              onChange={(e) => setTaskInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && e.ctrlKey) runTask(); }}
              placeholder="Descreva a tarefa para o agente... (Ctrl+Enter para executar)"
              rows={4}
              disabled={isRunning}
              className="w-full bg-white/5 border border-white/10 rounded-xl px-4 py-3 text-sm text-white placeholder-white/25 resize-none focus:outline-none focus:border-violet-500/50 focus:bg-white/8 transition-all disabled:opacity-50 leading-relaxed"
            />

            <div className="flex items-center gap-2 flex-wrap">
              {/* Model select */}
              <select
                value={model}
                onChange={(e) => setModel(e.target.value)}
                disabled={isRunning}
                className="bg-white/5 border border-white/10 rounded-lg px-3 py-1.5 text-xs text-white/70 focus:outline-none focus:border-violet-500/40 disabled:opacity-50 cursor-pointer"
              >
                {MODELS.map((m) => (
                  <option key={m.id} value={m.id} className="bg-[#111]">{m.label} — {m.desc}</option>
                ))}
              </select>

              {/* Example dropdown */}
              <div className="relative">
                <button
                  onClick={() => setShowExample((v) => !v)}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs text-white/50 hover:text-white/80 bg-white/5 hover:bg-white/10 border border-white/8 transition-all"
                >
                  <Terminal className="w-3.5 h-3.5" />
                  Ver exemplo completo
                  <ChevronRight className={`w-3 h-3 transition-transform ${showExample ? "rotate-90" : ""}`} />
                </button>

                <AnimatePresence>
                  {showExample && (
                    <motion.div
                      initial={{ opacity: 0, y: 4, scale: 0.97 }}
                      animate={{ opacity: 1, y: 0, scale: 1 }}
                      exit={{ opacity: 0, y: 4, scale: 0.97 }}
                      className="absolute left-0 top-full mt-2 z-50 w-[540px] bg-[#111] border border-white/10 rounded-xl shadow-2xl overflow-hidden"
                    >
                      <div className="flex items-center justify-between px-4 py-2.5 border-b border-white/8">
                        <span className="text-xs font-medium text-white/60">Exemplo — tarefa longa e complexa</span>
                        <button
                          onClick={copyExample}
                          className="flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-md bg-violet-600/20 text-violet-300 hover:bg-violet-600/40 transition-all"
                        >
                          {copied
                            ? <><Check className="w-3 h-3" /> Copiado!</>
                            : <><Copy className="w-3 h-3" /> Usar este exemplo</>
                          }
                        </button>
                      </div>
                      <pre className="text-[11px] text-white/50 p-4 max-h-72 overflow-y-auto leading-relaxed whitespace-pre-wrap font-mono">
                        {EXAMPLE_TASK}
                      </pre>
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>

              {/* Quick examples */}
              <div className="flex gap-1.5 flex-1 min-w-0">
                {QUICK_EXAMPLES.map((ex) => (
                  <button
                    key={ex}
                    onClick={() => { setTaskInput(ex); textareaRef.current?.focus(); }}
                    disabled={isRunning}
                    className="text-[10px] px-2 py-1 rounded-md bg-white/5 border border-white/8 text-white/40 hover:text-white/70 hover:bg-white/8 transition-all disabled:opacity-30 truncate"
                    title={ex}
                  >
                    {ex.substring(0, 35)}…
                  </button>
                ))}
              </div>

              {/* Clear stuck sessions button — shown on 429 */}
              {sessionLimitError && (
                <button
                  onClick={clearStuckSessions}
                  disabled={clearingStuck}
                  className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium bg-orange-500/20 text-orange-300 hover:bg-orange-500/30 border border-orange-500/20 transition-all disabled:opacity-50 shrink-0"
                >
                  {clearingStuck ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <XCircle className="w-3.5 h-3.5" />}
                  Limpar sessões
                </button>
              )}

              {/* Run/Stop */}
              <button
                onClick={isRunning ? stopTask : runTask}
                disabled={!isRunning && !taskInput.trim()}
                className={`ml-auto flex items-center gap-2 px-4 py-1.5 rounded-lg text-sm font-medium transition-all shrink-0 ${
                  isRunning
                    ? "bg-red-500/20 text-red-300 hover:bg-red-500/30 border border-red-500/20"
                    : "bg-violet-600 hover:bg-violet-500 text-white disabled:opacity-30"
                }`}
              >
                {isRunning
                  ? <><StopCircle className="w-4 h-4" /> Parar</>
                  : <><Play className="w-4 h-4" /> Executar</>
                }
              </button>
            </div>
          </div>

          {/* Human input prompt — shown when agent needs help */}
          <AnimatePresence>
            {currentTask?.waitingForHuman && (
              <motion.div
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: "auto" }}
                exit={{ opacity: 0, height: 0 }}
                className="px-4 py-3 border-b border-amber-500/20 bg-amber-500/8 shrink-0"
              >
                <div className="flex items-start gap-2 mb-2">
                  <HelpCircle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
                  <div>
                    <p className="text-xs font-semibold text-amber-300">O agente precisa da sua ajuda</p>
                    <p className="text-xs text-amber-200/70 mt-0.5">{currentTask.humanQuestion}</p>
                  </div>
                </div>
                <div className="flex gap-2">
                  <input
                    ref={humanInputRef}
                    type="text"
                    value={humanInput}
                    onChange={(e) => setHumanInput(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter") sendHumanResponse(); }}
                    placeholder="Digite sua resposta..."
                    className="flex-1 bg-white/5 border border-amber-500/30 rounded-lg px-3 py-2 text-sm text-white placeholder-white/25 focus:outline-none focus:border-amber-400/60 transition-all"
                  />
                  <button
                    onClick={sendHumanResponse}
                    disabled={!humanInput.trim() || sendingHuman}
                    className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-amber-500/20 text-amber-300 hover:bg-amber-500/35 border border-amber-500/25 text-xs font-medium disabled:opacity-40 transition-all"
                  >
                    {sendingHuman ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Send className="w-3.5 h-3.5" />}
                    Enviar
                  </button>
                </div>
              </motion.div>
            )}
          </AnimatePresence>

          {/* Live status bar */}
          <AnimatePresence>
            {currentTask && !currentTask.waitingForHuman && (
              <motion.div
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: "auto" }}
                exit={{ opacity: 0, height: 0 }}
                className={`px-4 py-2.5 border-b border-white/8 shrink-0 ${
                  currentTask.status === "completed" ? "bg-green-500/5" :
                  currentTask.status === "failed" ? "bg-red-500/5" :
                  "bg-violet-500/5"
                }`}
              >
                <div className="flex items-center gap-3">
                  {currentTask.status === "running" && <Loader2 className="w-3.5 h-3.5 text-violet-400 animate-spin shrink-0" />}
                  {currentTask.status === "completed" && <CheckCircle2 className="w-3.5 h-3.5 text-green-400 shrink-0" />}
                  {currentTask.status === "failed" && <XCircle className="w-3.5 h-3.5 text-red-400 shrink-0" />}

                  <div className="flex-1 min-w-0">
                    {currentTask.status === "running" && currentTask.currentAction
                      ? <p className="text-xs text-white/80 truncate">{currentTask.currentAction}</p>
                      : currentTask.status === "completed"
                      ? <p className="text-xs text-green-400">Tarefa concluída com sucesso</p>
                      : currentTask.status === "failed"
                      ? <p className="text-xs text-red-400">Tarefa falhou</p>
                      : <p className="text-xs text-white/50">Iniciando agente...</p>
                    }
                  </div>

                  {currentTask.currentStep != null && currentTask.status === "running" && (
                    <span className="text-[10px] text-white/30 shrink-0">Passo {currentTask.currentStep}</span>
                  )}

                  {currentTask.currentUrl && (
                    <span className="flex items-center gap-1 text-[10px] text-white/30 max-w-[160px] truncate shrink-0">
                      <Link2 className="w-3 h-3 shrink-0" />
                      <span className="truncate">{currentTask.currentUrl.replace(/^https?:\/\//, "")}</span>
                    </span>
                  )}
                </div>

                {currentTask.currentThought && currentTask.status === "running" && (
                  <div className="flex items-start gap-2 mt-1.5">
                    <Brain className="w-3 h-3 text-violet-400/60 mt-0.5 shrink-0" />
                    <p className="text-[11px] text-white/40 italic line-clamp-2">{currentTask.currentThought}</p>
                  </div>
                )}
              </motion.div>
            )}
          </AnimatePresence>

          {/* Steps log */}
          <div className="flex-1 overflow-y-auto p-4 space-y-2">
            {!currentTask && (
              <div className="flex flex-col items-center justify-center h-full gap-3 text-white/20">
                <Globe className="w-10 h-10" />
                <p className="text-sm">Escreva uma tarefa e clique em Executar</p>
                <p className="text-xs text-white/15">O agente vai navegar na web e fazer o que você pedir em tempo real</p>
              </div>
            )}

            <AnimatePresence initial={false}>
              {visibleEvents.map((event, i) => (
                <motion.div
                  key={i}
                  initial={{ opacity: 0, x: -8 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ duration: 0.15 }}
                  className={`flex gap-3 p-3 rounded-lg border text-xs ${
                    event.type === "done" ? "bg-green-500/5 border-green-500/10" :
                    event.type === "error" ? "bg-red-500/5 border-red-500/10" :
                    event.type === "started" ? "bg-yellow-500/5 border-yellow-500/10" :
                    event.type === "human_input_required" ? "bg-amber-500/5 border-amber-500/15" :
                    "bg-white/3 border-white/5"
                  }`}
                >
                  <StepIcon type={event.type} />
                  <div className="flex-1 min-w-0">
                    {event.type === "step" && (
                      <>
                        {event.action && <p className="text-white/75 font-medium">{event.action}</p>}
                        {event.thought && <p className="text-white/35 italic mt-0.5 text-[11px]">{event.thought}</p>}
                        {event.url && <p className="text-blue-400/60 mt-0.5 text-[10px] truncate">{event.url}</p>}
                      </>
                    )}
                    {event.type === "started" && <p className="text-white/60">{event.message}</p>}
                    {event.type === "human_input_required" && (
                      <>
                        <p className="text-amber-300 font-medium">Aguardando sua resposta</p>
                        {event.question && <p className="text-amber-200/60 mt-0.5 text-[11px]">{event.question}</p>}
                      </>
                    )}
                    {event.type === "done" && (
                      <>
                        <p className="text-green-400 font-medium">Concluído!</p>
                        {event.result && <p className="text-white/50 mt-1 whitespace-pre-wrap text-[11px]">{event.result}</p>}
                        {event.steps != null && <p className="text-white/25 mt-1 text-[10px]">{event.steps} passos executados</p>}
                      </>
                    )}
                    {event.type === "error" && <p className="text-red-400">{event.error}</p>}
                  </div>
                  <span className="text-white/20 text-[10px] shrink-0 mt-0.5">
                    {event.step != null ? `#${event.step}` : formatTime(event.timestamp)}
                  </span>
                </motion.div>
              ))}
            </AnimatePresence>
            <div ref={logsEndRef} />
          </div>
        </div>

        {/* Right panel - live screenshot */}
        <div className="w-96 shrink-0 flex flex-col bg-[#0d0d14]">
          <div className="flex items-center justify-between px-4 py-3 border-b border-white/8 shrink-0">
            <div className="flex items-center gap-2 text-xs text-white/50">
              <Eye className="w-3.5 h-3.5" />
              <span>Visualização ao vivo</span>
            </div>
            {isRunning && !currentTask?.waitingForHuman && (
              <span className="flex items-center gap-1.5 text-[10px] text-violet-400">
                <span className="w-1.5 h-1.5 rounded-full bg-violet-400 animate-pulse" />
                AO VIVO
              </span>
            )}
            {currentTask?.waitingForHuman && (
              <span className="flex items-center gap-1.5 text-[10px] text-amber-400">
                <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />
                AGUARDANDO
              </span>
            )}
          </div>

          <div className="flex-1 overflow-auto p-3 flex flex-col gap-3">
            {currentTask?.latestScreenshot ? (
              <motion.div
                key={currentTask.latestScreenshot.slice(-20)}
                initial={{ opacity: 0.6, scale: 0.98 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={{ duration: 0.2 }}
                className="relative"
              >
                <img
                  src={`data:image/png;base64,${currentTask.latestScreenshot}`}
                  alt="Browser screenshot"
                  className={`w-full rounded-xl border shadow-lg ${
                    currentTask.waitingForHuman ? "border-amber-500/30" : "border-white/10"
                  }`}
                />
                {isRunning && !currentTask.waitingForHuman && (
                  <div className="absolute top-2 right-2 flex items-center gap-1 bg-black/70 rounded-md px-2 py-1 text-[10px] text-white/70 backdrop-blur">
                    <Loader2 className="w-2.5 h-2.5 animate-spin" />
                    Atualizando...
                  </div>
                )}
                {currentTask.waitingForHuman && (
                  <div className="absolute top-2 right-2 flex items-center gap-1 bg-amber-900/80 rounded-md px-2 py-1 text-[10px] text-amber-300 backdrop-blur border border-amber-500/30">
                    <HelpCircle className="w-2.5 h-2.5" />
                    Precisa de ajuda
                  </div>
                )}
              </motion.div>
            ) : (
              <div className="flex flex-col items-center justify-center min-h-48 flex-1 gap-3 text-white/15 border border-white/5 rounded-xl">
                <ImageIcon className="w-10 h-10" />
                <p className="text-xs">O navegador aparece aqui ao vivo</p>
              </div>
            )}

            <AnimatePresence>
              {currentTask?.waitingForHuman && (
                <motion.div
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="p-3 rounded-xl bg-amber-500/10 border border-amber-500/20"
                >
                  <div className="flex items-center gap-2 mb-1.5">
                    <HelpCircle className="w-4 h-4 text-amber-400" />
                    <span className="text-xs font-medium text-amber-300">O agente precisa de você</span>
                  </div>
                  <p className="text-xs text-amber-200/60 mb-2">{currentTask.humanQuestion}</p>
                  <p className="text-[10px] text-white/30">Responda no painel à esquerda ↙</p>
                </motion.div>
              )}
              {currentTask?.status === "completed" && (
                <motion.div
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="p-3 rounded-xl bg-green-500/10 border border-green-500/20"
                >
                  <div className="flex items-center gap-2 mb-2">
                    <CheckCircle2 className="w-4 h-4 text-green-400" />
                    <span className="text-xs font-medium text-green-400">Tarefa concluída</span>
                  </div>
                  {visibleEvents.find(e => e.type === "done")?.result && (
                    <p className="text-xs text-white/50 whitespace-pre-wrap leading-relaxed">
                      {visibleEvents.find(e => e.type === "done")?.result}
                    </p>
                  )}
                </motion.div>
              )}
              {currentTask?.status === "failed" && (
                <motion.div
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="p-3 rounded-xl bg-red-500/10 border border-red-500/20"
                >
                  <div className="flex items-center gap-2 mb-1">
                    <XCircle className="w-4 h-4 text-red-400" />
                    <span className="text-xs font-medium text-red-400">Tarefa falhou</span>
                  </div>
                  {visibleEvents.find(e => e.type === "error")?.error && (
                    <p className="text-xs text-red-300/60">{visibleEvents.find(e => e.type === "error")?.error}</p>
                  )}
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        </div>
      </div>
    </div>
  );
}
