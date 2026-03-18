import { useState, useCallback, useRef, useEffect } from "react";
import { useSendMessage } from "@workspace/api-client-react";
import type { ChatMessage, TokenUsage } from "@workspace/api-client-react/src/generated/api.schemas";
import { useToast } from "@/hooks/use-toast";

export interface Message extends ChatMessage {
  id: string;
  timestamp: Date;
}

export type ChatModel = "deepseek-v3.2" | "gemini-3-flash-preview" | "kimi-k2.5";

export function useChatSession() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [model, setModel] = useState<ChatModel>("deepseek-v3.2");
  const [systemPrompt, setSystemPrompt] = useState<string>("You are a highly capable, helpful, and concise AI assistant running via Ollama.");
  const [tokenUsage, setTokenUsage] = useState<TokenUsage | null>(null);
  const [isTyping, setIsTyping] = useState(false);
  
  const { toast } = useToast();
  const sendMessageMutation = useSendMessage();
  
  // Auto-scroll anchor ref
  const messagesEndRef = useRef<HTMLDivElement>(null);
  
  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, scrollToBottom, isTyping]);

  const clearChat = useCallback(() => {
    setMessages([]);
    setTokenUsage(null);
    toast({
      title: "Chat cleared",
      description: "Started a new conversation session.",
    });
  }, [toast]);

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
      // Build API request history
      const apiMessages: ChatMessage[] = [];
      
      // Prepend system prompt if it exists
      if (systemPrompt.trim()) {
        apiMessages.push({ role: "system", content: systemPrompt.trim() });
      }
      
      // Add conversation history
      // We map over current state + the new message we just created locally
      const historyToSync = [...messages, newUserMessage].map(({ role, content }) => ({
        role,
        content,
      }));
      
      apiMessages.push(...historyToSync);

      const response = await sendMessageMutation.mutateAsync({
        data: {
          messages: apiMessages,
          model: model,
          stream: false, // Set to false based on schema, though UI can feel streamed
        },
      });

      const newAiMessage: Message = {
        id: response.id || crypto.randomUUID(),
        role: "assistant" as const, // Forcing type from string
        content: response.content,
        timestamp: new Date(),
      };

      setMessages((prev) => [...prev, newAiMessage]);
      
      if (response.usage) {
        setTokenUsage(response.usage);
      }

    } catch (error: any) {
      console.error("Failed to send message:", error);
      toast({
        variant: "destructive",
        title: "Communication Error",
        description: error.message || "Failed to get a response from Ollama. Please try again.",
      });
      // Optionally remove the user message if it failed, but usually it's better to leave it and let them retry
    } finally {
      setIsTyping(false);
    }
  }, [messages, model, systemPrompt, sendMessageMutation, toast]);

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
