import { useState, useCallback } from "react";

export interface Message {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  timestamp: Date;
  commands?: ParsedCommand[];
}

export interface ParsedCommand {
  type: "stop" | "new_task" | "respond";
  param?: string;
}

export type NovaModel = "llama-3.3-70b-versatile" | "llama-3.1-8b-instant" | "mixtral-8x7b-32768";

function parseCommands(text: string): { cleanText: string; commands: ParsedCommand[] } {
  const commands: ParsedCommand[] = [];
  let cleanText = text;

  const stopMatch = text.match(/\[CMD:stop\]/gi);
  if (stopMatch) {
    commands.push({ type: "stop" });
    cleanText = cleanText.replace(/\[CMD:stop\]/gi, "").trim();
  }

  const taskMatches = [...text.matchAll(/\[CMD:new_task:([^\]]+)\]/gi)];
  for (const m of taskMatches) {
    commands.push({ type: "new_task", param: m[1].trim() });
    cleanText = cleanText.replace(m[0], "").trim();
  }

  const respondMatches = [...text.matchAll(/\[CMD:respond:([^\]]+)\]/gi)];
  for (const m of respondMatches) {
    commands.push({ type: "respond", param: m[1].trim() });
    cleanText = cleanText.replace(m[0], "").trim();
  }

  return { cleanText, commands };
}

interface UseChatSessionOptions {
  browserContextForPrompt?: () => string;
  onCommand?: (cmd: ParsedCommand) => void;
}

export function useChatSession({ browserContextForPrompt, onCommand }: UseChatSessionOptions = {}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [model, setModel] = useState<NovaModel>("llama-3.3-70b-versatile");
  const [systemPrompt, setSystemPrompt] = useState<string>(
    "Você é Nova, uma assistente de IA altamente capaz, útil e concisa. Responda sempre no mesmo idioma do usuário."
  );
  const [isTyping, setIsTyping] = useState(false);
  const [streamingContent, setStreamingContent] = useState<string>("");

  const clearChat = useCallback(() => {
    setMessages([]);
    setStreamingContent("");
  }, []);

  const sendMessage = useCallback(async (content: string) => {
    if (!content.trim()) return;

    const newUserMessage: Message = {
      id: crypto.randomUUID(),
      role: "user",
      content: content.trim(),
      timestamp: new Date(),
    };

    setMessages((prev) => [...prev, newUserMessage]);
    setIsTyping(true);
    setStreamingContent("");

    try {
      const browserCtx = browserContextForPrompt?.();
      const fullSystemPrompt = browserCtx
        ? `${systemPrompt}\n\n${browserCtx}`
        : systemPrompt;

      const apiMessages: Array<{ role: string; content: string }> = [];
      if (fullSystemPrompt.trim()) {
        apiMessages.push({ role: "system", content: fullSystemPrompt.trim() });
      }

      const allMessages = [...messages, newUserMessage];
      apiMessages.push(...allMessages.map(({ role, content: c }) => ({ role, content: c })));

      const basePath = (import.meta.env.BASE_URL ?? "/").replace(/\/$/, "");
      const response = await fetch(`${basePath}/api/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: apiMessages, model }),
      });

      if (!response.ok || !response.body) {
        throw new Error(`HTTP ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let accumulated = "";
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed || trimmed === "data: [DONE]") continue;
          if (!trimmed.startsWith("data: ")) continue;

          const jsonStr = trimmed.slice(6);
          try {
            const chunk = JSON.parse(jsonStr);
            if (chunk.error) throw new Error(chunk.error);
            const delta = chunk.choices?.[0]?.delta?.content;
            if (delta) {
              accumulated += delta;
              setStreamingContent(accumulated);
            }
          } catch (parseErr: any) {
            if (parseErr.message && !parseErr.message.includes("JSON")) {
              throw parseErr;
            }
          }
        }
      }

      const finalContent = accumulated;
      const { cleanText, commands } = parseCommands(finalContent);

      const newAiMessage: Message = {
        id: crypto.randomUUID(),
        role: "assistant",
        content: cleanText || finalContent,
        timestamp: new Date(),
        commands: commands.length > 0 ? commands : undefined,
      };

      setMessages((prev) => [...prev, newAiMessage]);
      setStreamingContent("");

      for (const cmd of commands) {
        onCommand?.(cmd);
      }
    } catch (error: any) {
      const errMsg: Message = {
        id: crypto.randomUUID(),
        role: "assistant",
        content: `Erro: ${error.message || "Falha ao obter resposta. Tente novamente."}`,
        timestamp: new Date(),
      };
      setMessages((prev) => [...prev, errMsg]);
      setStreamingContent("");
    } finally {
      setIsTyping(false);
    }
  }, [messages, model, systemPrompt, browserContextForPrompt, onCommand]);

  return {
    messages,
    model,
    setModel,
    systemPrompt,
    setSystemPrompt,
    isTyping,
    streamingContent,
    sendMessage,
    clearChat,
  };
}
