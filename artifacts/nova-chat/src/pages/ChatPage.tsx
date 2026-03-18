import React from "react";
import { Sparkles, Loader2, Menu } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { useChatSession } from "@/hooks/use-chat-session";
import { MessageBubble } from "@/components/MessageBubble";
import { ChatInput } from "@/components/ChatInput";
import { SettingsPanel } from "@/components/SettingsPanel";

export default function ChatPage() {
  const {
    messages,
    model,
    setModel,
    systemPrompt,
    setSystemPrompt,
    tokenUsage,
    isTyping,
    sendMessage,
    clearChat,
    messagesEndRef
  } = useChatSession();

  const [mobileMenuOpen, setMobileMenuOpen] = React.useState(false);

  return (
    <div className="flex h-screen w-full overflow-hidden bg-background">
      {/* Background visual effect */}
      <div className="fixed inset-0 pointer-events-none z-0">
        <img 
          src={`${import.meta.env.BASE_URL}images/nova-bg.png`}
          alt="Background Texture"
          className="w-full h-full object-cover opacity-20 mix-blend-screen"
        />
      </div>

      {/* Main Chat Area */}
      <div className="flex-1 flex flex-col relative z-10 min-w-0">
        {/* Header */}
        <header className="h-16 flex-shrink-0 flex items-center justify-between px-6 border-b border-white/5 bg-black/20 backdrop-blur-md">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-primary/20 border border-primary/30 flex items-center justify-center">
              <Sparkles className="w-5 h-5 text-primary" />
            </div>
            <h1 className="font-display font-bold text-xl tracking-tight bg-gradient-to-r from-white to-white/60 bg-clip-text text-transparent">
              Ollama Chat
            </h1>
            <span className="hidden md:inline-flex items-center px-2.5 py-0.5 rounded-full text-[10px] font-medium bg-white/5 border border-white/10 text-muted-foreground ml-2">
              {model}
            </span>
          </div>

          <button 
            className="lg:hidden p-2 rounded-md hover:bg-white/10 text-muted-foreground transition-colors"
            onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
          >
            <Menu className="w-5 h-5" />
          </button>
        </header>

        {/* Messages Scroll Area */}
        <div className="flex-1 overflow-y-auto overflow-x-hidden p-4 md:p-6 lg:p-8 scroll-smooth">
          <div className="max-w-4xl mx-auto flex flex-col min-h-full">
            {messages.length === 0 ? (
              <div className="flex-1 flex flex-col items-center justify-center text-center opacity-0 animate-in fade-in zoom-in duration-700">
                <div className="w-20 h-20 rounded-3xl bg-gradient-to-tr from-primary/20 to-violet-500/20 border border-white/10 flex items-center justify-center mb-8 shadow-2xl shadow-primary/10">
                  <Sparkles className="w-10 h-10 text-white opacity-80" />
                </div>
                <h2 className="font-display text-3xl font-bold mb-4 text-foreground">
                  How can I help you today?
                </h2>
                <p className="text-muted-foreground max-w-md mb-8 leading-relaxed">
                  I run locally with Ollama, ready to assist with writing, coding, analysis, and multimodal tasks.
                </p>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 w-full max-w-2xl">
                  {[
                    "Write a Python script to parse CSV data",
                    "Explain quantum computing in simple terms",
                    "Help me draft a professional email to my boss",
                    "Brainstorm 5 creative startup ideas"
                  ].map((suggestion, i) => (
                    <button
                      key={i}
                      onClick={() => sendMessage(suggestion)}
                      className="p-4 rounded-2xl bg-white/[0.03] border border-white/[0.05] hover:bg-white/[0.08] hover:border-white/10 text-left transition-all duration-300 hover:-translate-y-1 hover:shadow-lg"
                    >
                      <p className="text-sm font-medium text-foreground/80">{suggestion}</p>
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="flex flex-col w-full pb-10">
                {messages.map((msg) => (
                  <MessageBubble key={msg.id} message={msg} />
                ))}
                
                {isTyping && (
                  <motion.div 
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    className="flex items-center gap-3 text-muted-foreground mb-6 pl-4"
                  >
                    <div className="flex h-8 w-8 items-center justify-center rounded-full bg-primary/20 border border-primary/30">
                      <Loader2 className="w-4 h-4 text-primary animate-spin" />
                    </div>
                    <span className="text-sm font-medium animate-pulse">Model is thinking...</span>
                  </motion.div>
                )}
                
                {/* Auto-scroll anchor */}
                <div ref={messagesEndRef} className="h-px w-full" />
              </div>
            )}
          </div>
        </div>

        {/* Input Area */}
        <div className="flex-shrink-0 bg-gradient-to-t from-background via-background/95 to-transparent pt-6">
          <ChatInput onSend={sendMessage} isTyping={isTyping} />
        </div>
      </div>

      {/* Settings Sidebar (Desktop) */}
      <SettingsPanel
        model={model}
        setModel={setModel}
        systemPrompt={systemPrompt}
        setSystemPrompt={setSystemPrompt}
        tokenUsage={tokenUsage}
        onNewChat={clearChat}
      />

      {/* Mobile Settings Overlay */}
      <AnimatePresence>
        {mobileMenuOpen && (
          <>
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => setMobileMenuOpen(false)}
              className="fixed inset-0 bg-black/60 backdrop-blur-sm z-40 lg:hidden"
            />
            <motion.div
              initial={{ x: "100%" }}
              animate={{ x: 0 }}
              exit={{ x: "100%" }}
              transition={{ type: "spring", damping: 25, stiffness: 200 }}
              className="fixed right-0 top-0 bottom-0 w-4/5 max-w-[320px] bg-background border-l border-white/10 z-50 shadow-2xl"
            >
              <SettingsPanel
                model={model}
                setModel={(m) => { setModel(m); setMobileMenuOpen(false); }}
                systemPrompt={systemPrompt}
                setSystemPrompt={setSystemPrompt}
                tokenUsage={tokenUsage}
                onNewChat={() => { clearChat(); setMobileMenuOpen(false); }}
              />
            </motion.div>
          </>
        )}
      </AnimatePresence>
    </div>
  );
}
