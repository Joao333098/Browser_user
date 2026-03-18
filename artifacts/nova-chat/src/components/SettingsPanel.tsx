import React from "react";
import { Settings2, Zap, BrainCircuit, Hash, ChevronRight, RefreshCw } from "lucide-react";
import type { NovaModel } from "@/hooks/use-chat-session";
import type { TokenUsage } from "@workspace/api-client-react/src/generated/api.schemas";
import { motion, AnimatePresence } from "framer-motion";

interface SettingsPanelProps {
  model: NovaModel;
  setModel: (m: NovaModel) => void;
  systemPrompt: string;
  setSystemPrompt: (s: string) => void;
  tokenUsage: TokenUsage | null;
  onNewChat: () => void;
}

const MODELS: { id: NovaModel; name: string; desc: string }[] = [
  { id: "deepseek-v3.2", name: "DeepSeek V3.2", desc: "Fast & balanced (Recommended)" },
  { id: "gemini-3-flash-preview", name: "Gemini 3 Flash", desc: "Google's fast multimodal model" },
  { id: "kimi-k2.5", name: "Kimi K2.5", desc: "Moonshot AI's reasoning model" },
];

export function SettingsPanel({
  model,
  setModel,
  systemPrompt,
  setSystemPrompt,
  tokenUsage,
  onNewChat
}: SettingsPanelProps) {
  const [isOpen, setIsOpen] = React.useState(false);

  return (
    <div className="hidden lg:flex flex-col w-[320px] border-l border-white/5 bg-black/20 backdrop-blur-3xl h-full p-6 overflow-y-auto">
      <div className="flex items-center justify-between mb-8">
        <h2 className="text-lg font-display text-foreground flex items-center gap-2">
          <Settings2 className="w-5 h-5 text-primary" />
          Configuration
        </h2>
      </div>

      <button
        onClick={onNewChat}
        className="w-full flex items-center justify-center gap-2 mb-8 bg-white/5 hover:bg-white/10 border border-white/10 text-foreground py-3 px-4 rounded-xl font-medium transition-all hover:shadow-lg hover:shadow-white/5 active:scale-[0.98]"
      >
        <RefreshCw className="w-4 h-4" />
        Start New Chat
      </button>

      <div className="space-y-8">
        {/* Model Selection */}
        <div className="space-y-4">
          <div className="flex items-center gap-2 text-sm font-semibold text-muted-foreground uppercase tracking-wider">
            <Zap className="w-4 h-4" />
            Model
          </div>
          <div className="grid gap-2">
            {MODELS.map((m) => (
              <button
                key={m.id}
                onClick={() => setModel(m.id)}
                className={`flex flex-col items-start p-3 rounded-xl border text-left transition-all ${
                  model === m.id
                    ? "bg-primary/10 border-primary/50 ring-1 ring-primary/20"
                    : "bg-card border-white/5 hover:border-white/20 hover:bg-white/5"
                }`}
              >
                <span className={`font-semibold text-sm ${model === m.id ? "text-primary" : "text-foreground"}`}>
                  {m.name}
                </span>
                <span className="text-xs text-muted-foreground mt-0.5">{m.desc}</span>
              </button>
            ))}
          </div>
        </div>

        {/* System Prompt */}
        <div className="space-y-4">
          <div className="flex items-center gap-2 text-sm font-semibold text-muted-foreground uppercase tracking-wider">
            <BrainCircuit className="w-4 h-4" />
            System Prompt
          </div>
          <textarea
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            className="w-full bg-card border border-white/5 rounded-xl p-3 text-sm text-foreground/90 placeholder:text-muted-foreground/50 focus:outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all resize-none"
            rows={5}
            placeholder="Define the AI's persona..."
          />
        </div>

        {/* Token Stats */}
        <AnimatePresence>
          {tokenUsage && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              className="space-y-4 overflow-hidden"
            >
              <div className="flex items-center gap-2 text-sm font-semibold text-muted-foreground uppercase tracking-wider">
                <Hash className="w-4 h-4" />
                Latest Session Usage
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div className="bg-card border border-white/5 rounded-xl p-3 flex flex-col items-center justify-center">
                  <span className="text-xs text-muted-foreground mb-1">Prompt</span>
                  <span className="font-mono text-lg text-foreground font-semibold">
                    {tokenUsage.prompt_tokens.toLocaleString()}
                  </span>
                </div>
                <div className="bg-card border border-white/5 rounded-xl p-3 flex flex-col items-center justify-center">
                  <span className="text-xs text-muted-foreground mb-1">Completion</span>
                  <span className="font-mono text-lg text-foreground font-semibold">
                    {tokenUsage.completion_tokens.toLocaleString()}
                  </span>
                </div>
                <div className="col-span-2 bg-primary/5 border border-primary/20 rounded-xl p-3 flex flex-col items-center justify-center">
                  <span className="text-xs text-primary/80 mb-1">Total Tokens</span>
                  <span className="font-mono text-xl text-primary font-bold">
                    {tokenUsage.total_tokens.toLocaleString()}
                  </span>
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}
