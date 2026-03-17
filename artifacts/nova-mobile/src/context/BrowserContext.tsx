import React, { createContext, useContext, useRef, useState, useCallback } from "react";

export type TaskStatus = "running" | "completed" | "failed";

export type EventType =
  | "connected" | "started" | "step" | "done"
  | "error" | "ping" | "stream_end" | "human_input_required";

export interface AgentEvent {
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

export interface BrowserTask {
  id: string;
  status: TaskStatus;
  events: AgentEvent[];
  latestScreenshot?: string;
  currentAction?: string;
  currentThought?: string;
  currentUrl?: string;
  currentStep?: number;
  waitingForHuman?: boolean;
  humanQuestion?: string;
}

interface BrowserContextValue {
  task: BrowserTask | null;
  isRunning: boolean;
  runTask: (taskText: string, model: string) => Promise<void>;
  stopTask: () => void;
  respondToHuman: (response: string) => Promise<void>;
  clearTask: () => void;
}

const BrowserContext = createContext<BrowserContextValue | null>(null);

export function BrowserProvider({ children }: { children: React.ReactNode }) {
  const [task, setTask] = useState<BrowserTask | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const esRef = useRef<EventSource | null>(null);

  const stopTask = useCallback(() => {
    esRef.current?.close();
    setIsRunning(false);
    setTask((t) => t ? { ...t, status: "failed", waitingForHuman: false } : t);
  }, []);

  const clearTask = useCallback(() => {
    esRef.current?.close();
    setIsRunning(false);
    setTask(null);
  }, []);

  const respondToHuman = useCallback(async (response: string) => {
    if (!task) return;
    try {
      await fetch(`/api/browser/tasks/${task.id}/respond`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ response }),
      });
      setTask((t) => t ? { ...t, waitingForHuman: false, humanQuestion: undefined } : t);
    } catch {
      // ignore
    }
  }, [task]);

  const runTask = useCallback(async (taskText: string, model: string) => {
    esRef.current?.close();
    setIsRunning(true);
    setTask(null);

    try {
      const res = await fetch("/api/browser/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task: taskText.trim(), model }),
      });

      if (!res.ok) {
        const err = await res.json();
        setTask({
          id: "err",
          status: "failed",
          events: [{ type: "error", error: err.message || "Falha ao iniciar tarefa" }],
        });
        setIsRunning(false);
        return;
      }

      const { task_id } = await res.json();
      const newTask: BrowserTask = { id: task_id, status: "running", events: [] };
      setTask(newTask);

      const es = new EventSource(`/api/browser/stream/${task_id}`);
      esRef.current = es;

      es.onmessage = (e) => {
        const event: AgentEvent = JSON.parse(e.data);
        setTask((prev) => {
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

          return {
            ...prev, events, status, latestScreenshot, currentAction,
            currentThought, currentUrl, currentStep, waitingForHuman, humanQuestion,
          };
        });

        if (event.type === "done" || event.type === "error" || event.type === "stream_end") {
          es.close();
          setIsRunning(false);
        }
      };

      es.onerror = () => {
        es.close();
        setIsRunning(false);
        setTask((prev) => prev ? { ...prev, status: "failed", waitingForHuman: false } : prev);
      };
    } catch {
      setTask({ id: "err", status: "failed", events: [{ type: "error", error: "Erro de conexão" }] });
      setIsRunning(false);
    }
  }, []);

  return (
    <BrowserContext.Provider value={{ task, isRunning, runTask, stopTask, respondToHuman, clearTask }}>
      {children}
    </BrowserContext.Provider>
  );
}

export function useBrowser() {
  const ctx = useContext(BrowserContext);
  if (!ctx) throw new Error("useBrowser must be used inside BrowserProvider");
  return ctx;
}
