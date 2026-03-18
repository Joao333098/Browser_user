import { useState, useCallback, useRef, useEffect } from "react";
import { useSendMessage } from "@workspace/api-client-react";
import type { ChatMessage, TokenUsage } from "@workspace/api-client-react/src/generated/api.schemas";

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

export type ChatModel = "deepseek-v3.2" | "gemini-3-flash-preview" | "kimi-k2.5";

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
  const [model, setModel] = useState<ChatModel>("deepseek-v3.2");
  const [systemPrompt, setSystemPrompt] = useState<string>(
    "Você é uma assistente de IA altamente capaz, útil e concisa, executando localmente via Ollama. Responda sempre no mesmo idioma do usuário."
  );
  const [tokenUsage, setTokenUsage] = useState<TokenUsage | null>(null);
  const [isTyping, setIsTyping] = useState(false);

  const sendMessageMutation = useSendMessage();
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isTyping]);

  const clearChat = useCallback(() => {
    setMessages([]);
    setTokenUsage(null);
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

    try {
      const apiMessages: ChatMessage[] = [];

      const browserCtx = browserContextForPrompt?.();
      const fullSystemPrompt = browserCtx
        ? `${systemPrompt}\n\n${browserCtx}`
        : systemPrompt;

      if (fullSystemPrompt.trim()) {
        apiMessages.push({ role: "system", content: fullSystemPrompt.trim() });
      }

      const history = [...messages, newUserMessage].map(({ role, content }) => ({ role, content }));
      apiMessages.push(...history);

      const response = await sendMessageMutation.mutateAsync({
        data: { messages: apiMessages, model, stream: false },
      });

      const rawContent = response.content;
      const { cleanText, commands } = parseCommands(rawContent);

      const newAiMessage: Message = {
        id: response.id || crypto.randomUUID(),
        role: "assistant",
        content: cleanText || rawContent,
        timestamp: new Date(),
        commands: commands.length > 0 ? commands : undefined,
      };

      setMessages((prev) => [...prev, newAiMessage]);

      for (const cmd of commands) {
        onCommand?.(cmd);
      }

      if (response.usage) setTokenUsage(response.usage);
    } catch (error: any) {
      const errMsg: Message = {
        id: crypto.randomUUID(),
        role: "assistant",
        content: `Erro: ${error.message || "Falha ao obter resposta. Tente novamente."}`,
        timestamp: new Date(),
      };
      setMessages((prev) => [...prev, errMsg]);
    } finally {
      setIsTyping(false);
    }
  }, [messages, model, systemPrompt, sendMessageMutation, browserContextForPrompt, onCommand]);

  return {
    messages,
    model,
    setModel,
    systemPrompt,
    setSystemPrompt,
    tokenUsage,
    isTyping,
    sendMessage,
    clearChat,
    messagesEndRef,
  };
}
