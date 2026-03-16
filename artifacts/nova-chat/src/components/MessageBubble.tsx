import React, { useState } from "react";
import { motion } from "framer-motion";
import { format } from "date-fns";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { vscDarkPlus } from "react-syntax-highlighter/dist/esm/styles/prism";
import { Check, Copy, Sparkles, User } from "lucide-react";
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import type { Message } from "@/hooks/use-chat-session";

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

interface MessageBubbleProps {
  message: Message;
}

export function MessageBubble({ message }: MessageBubbleProps) {
  const isUser = message.role === "user";
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(message.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 10, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: 0.3, ease: "easeOut" }}
      className={cn(
        "flex w-full mb-6 group",
        isUser ? "justify-end" : "justify-start"
      )}
    >
      <div
        className={cn(
          "flex max-w-[85%] md:max-w-[75%] lg:max-w-[65%]",
          isUser ? "flex-row-reverse" : "flex-row"
        )}
      >
        {/* Avatar */}
        <div className="flex-shrink-0 mx-3 mt-1">
          <div
            className={cn(
              "flex h-8 w-8 items-center justify-center rounded-full shadow-lg border",
              isUser
                ? "bg-secondary border-white/10"
                : "bg-primary border-primary/50 text-white shadow-primary/20"
            )}
          >
            {isUser ? (
              <User className="h-4 w-4 text-muted-foreground" />
            ) : (
              <Sparkles className="h-4 w-4" />
            )}
          </div>
        </div>

        {/* Message Content */}
        <div className="flex flex-col min-w-0">
          <div className={cn("flex items-center mb-1", isUser ? "justify-end" : "justify-start")}>
            <span className="text-xs font-medium text-muted-foreground px-1">
              {isUser ? "You" : "Nova AI"}
            </span>
            <span className="text-[10px] text-muted-foreground/50 ml-2">
              {format(message.timestamp, "h:mm a")}
            </span>
          </div>

          <div
            className={cn(
              "relative rounded-2xl px-5 py-4 shadow-sm",
              isUser
                ? "bg-white/5 border border-white/10 text-foreground"
                : "bg-gradient-to-br from-white/5 to-transparent border border-white/[0.05] text-foreground/90 backdrop-blur-sm"
            )}
          >
            {isUser ? (
              <div className="whitespace-pre-wrap break-words leading-relaxed">
                {message.content}
              </div>
            ) : (
              <div className="prose prose-invert prose-p:leading-relaxed prose-pre:m-0 break-words">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={{
                    code({ node, inline, className, children, ...props }: any) {
                      const match = /language-(\w+)/.exec(className || "");
                      return !inline && match ? (
                        <div className="relative group/code rounded-xl overflow-hidden my-4 border border-white/10">
                          <div className="flex items-center justify-between bg-black/50 px-4 py-2 border-b border-white/10">
                            <span className="text-xs font-mono text-muted-foreground">
                              {match[1]}
                            </span>
                          </div>
                          <SyntaxHighlighter
                            {...props}
                            style={vscDarkPlus}
                            language={match[1]}
                            PreTag="div"
                            customStyle={{ margin: 0, padding: '1rem', background: 'transparent' }}
                          >
                            {String(children).replace(/\n$/, "")}
                          </SyntaxHighlighter>
                        </div>
                      ) : (
                        <code {...props} className={className}>
                          {children}
                        </code>
                      );
                    },
                  }}
                >
                  {message.content}
                </ReactMarkdown>
              </div>
            )}
          </div>

          {/* Actions */}
          <div
            className={cn(
              "flex items-center mt-2 opacity-0 group-hover:opacity-100 transition-opacity",
              isUser ? "justify-end" : "justify-start"
            )}
          >
            <button
              onClick={handleCopy}
              className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors bg-white/5 hover:bg-white/10 px-2 py-1 rounded-md border border-white/5"
            >
              {copied ? <Check className="h-3 w-3 text-green-400" /> : <Copy className="h-3 w-3" />}
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
        </div>
      </div>
    </motion.div>
  );
}
