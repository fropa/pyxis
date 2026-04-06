import { useState, useRef, useEffect } from "react";
import { useMutation } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import { Send, Bot, User, Loader2, Trash2 } from "lucide-react";
import { api, getErrorMessage } from "../api/client";
import clsx from "clsx";

interface Message {
  role: "user" | "assistant";
  content: string;
}

const STARTERS = [
  "What's currently broken?",
  "Any open incidents right now?",
  "Which services have the highest error rate?",
  "Were there any deploys in the last hour?",
];

export default function AssistantView() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const mutation = useMutation({
    mutationFn: (question: string) =>
      api.assistant.chat({ question, history: messages }),
    onSuccess: (data, question) => {
      setMessages((prev) => [
        ...prev,
        { role: "user", content: question },
        { role: "assistant", content: data.answer },
      ]);
    },
  });

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, mutation.isPending]);

  const send = (text: string) => {
    const q = text.trim();
    if (!q || mutation.isPending) return;
    setInput("");
    mutation.mutate(q);
  };

  const handleKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(input);
    }
  };

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-border bg-surface flex-shrink-0">
        <div>
          <h1 className="text-[15px] font-semibold text-text-1 flex items-center gap-2">
            <Bot size={16} className="text-accent" />
            On-call Assistant
          </h1>
          <p className="text-[12px] text-text-3 mt-0.5">
            Ask anything about your infrastructure — incidents, services, deploys
          </p>
        </div>
        {messages.length > 0 && (
          <button
            onClick={() => setMessages([])}
            className="flex items-center gap-1.5 px-3 py-1.5 text-[12px] text-text-3 hover:text-danger border border-border hover:border-danger/30 rounded-lg transition-all"
          >
            <Trash2 size={12} />
            Clear
          </button>
        )}
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
        {messages.length === 0 && !mutation.isPending && (
          <div className="flex flex-col items-center justify-center h-full gap-6 text-center">
            <div className="w-14 h-14 rounded-2xl bg-accent/10 border border-accent/20 flex items-center justify-center">
              <Bot size={24} className="text-accent" />
            </div>
            <div>
              <p className="text-[14px] font-semibold text-text-1 mb-1">
                Ask about your infrastructure
              </p>
              <p className="text-[12px] text-text-3 max-w-xs">
                I have real-time visibility into your incidents, services, deploys, and nodes.
              </p>
            </div>
            <div className="grid grid-cols-2 gap-2 w-full max-w-md">
              {STARTERS.map((s) => (
                <button
                  key={s}
                  onClick={() => send(s)}
                  className="px-3 py-2.5 text-[12px] text-text-2 bg-raised border border-border rounded-xl hover:border-accent/30 hover:text-accent transition-all text-left"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m, i) => (
          <div
            key={i}
            className={clsx("flex gap-3", m.role === "user" ? "justify-end" : "justify-start")}
          >
            {m.role === "assistant" && (
              <div className="w-7 h-7 rounded-lg bg-accent/10 border border-accent/20 flex items-center justify-center flex-shrink-0 mt-0.5">
                <Bot size={13} className="text-accent" />
              </div>
            )}
            <div
              className={clsx(
                "max-w-[80%] rounded-2xl px-4 py-3 text-[13px]",
                m.role === "user"
                  ? "bg-accent text-white rounded-tr-sm"
                  : "bg-raised border border-border text-text-1 rounded-tl-sm"
              )}
            >
              {m.role === "assistant" ? (
                <div className="prose-content max-w-none">
                  <ReactMarkdown>{m.content}</ReactMarkdown>
                </div>
              ) : (
                <p className="whitespace-pre-wrap">{m.content}</p>
              )}
            </div>
            {m.role === "user" && (
              <div className="w-7 h-7 rounded-lg bg-surface border border-border flex items-center justify-center flex-shrink-0 mt-0.5">
                <User size={13} className="text-text-3" />
              </div>
            )}
          </div>
        ))}

        {mutation.isPending && (
          <div className="flex gap-3 justify-start">
            <div className="w-7 h-7 rounded-lg bg-accent/10 border border-accent/20 flex items-center justify-center flex-shrink-0 mt-0.5">
              <Bot size={13} className="text-accent" />
            </div>
            <div className="bg-raised border border-border rounded-2xl rounded-tl-sm px-4 py-3">
              <Loader2 size={14} className="text-accent animate-spin" />
            </div>
          </div>
        )}

        {mutation.isError && (
          <div className="mx-auto max-w-md text-[12px] text-danger bg-danger/5 border border-danger/20 rounded-xl px-4 py-3 text-center">
            {getErrorMessage(mutation.error)}
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="flex-shrink-0 px-6 py-4 border-t border-border bg-surface">
        <div className="flex gap-3 items-end max-w-3xl mx-auto">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKey}
            placeholder="Ask anything… (Enter to send, Shift+Enter for newline)"
            rows={1}
            className="flex-1 resize-none bg-raised border border-border rounded-xl px-4 py-2.5 text-[13px] text-text-1 placeholder:text-text-4 focus:outline-none focus:border-accent/50 transition-colors min-h-[42px] max-h-32 overflow-y-auto"
            style={{ height: "auto" }}
            onInput={(e) => {
              const t = e.currentTarget;
              t.style.height = "auto";
              t.style.height = Math.min(t.scrollHeight, 128) + "px";
            }}
          />
          <button
            onClick={() => send(input)}
            disabled={!input.trim() || mutation.isPending}
            className="flex-shrink-0 w-10 h-10 bg-accent hover:bg-accent/90 disabled:opacity-40 text-white rounded-xl flex items-center justify-center transition-all shadow-sm"
          >
            <Send size={14} />
          </button>
        </div>
      </div>
    </div>
  );
}
