"use client";

import { useEffect, useRef, useState } from "react";
import { Send, Bot, User, Loader2 } from "lucide-react";
import clsx from "clsx";
import { api, postJSON } from "@/lib/api";

interface Message {
  role: string;
  content: string;
}

export default function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(true);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    (async () => {
      try {
        const res = await api<{ messages: Message[] }>(
          "/api/gateway/history/main?limit=30"
        );
        const filtered = (res.messages || []).filter(
          (m) => m.role === "user" || m.role === "assistant"
        );
        setMessages(filtered);
      } catch {
        /* gateway not available */
      } finally {
        setLoadingHistory(false);
      }
    })();
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const sendMessage = async () => {
    const text = input.trim();
    if (!text || sending) return;

    setInput("");
    setSending(true);
    setMessages((prev) => [...prev, { role: "user", content: text }]);

    try {
      const res = await postJSON<{ ok: boolean; response: string }>(
        "/api/gateway/chat",
        { message: text }
      );
      if (res.response) {
        setMessages((prev) => [
          ...prev,
          { role: "assistant", content: res.response },
        ]);
      }
    } catch (e: any) {
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: `Error: ${e.message || "Failed to get response"}`,
        },
      ]);
    } finally {
      setSending(false);
      inputRef.current?.focus();
    }
  };

  return (
    <div className="flex flex-col h-[calc(100vh-3rem)] fade-in">
      {/* Header */}
      <div className="flex-shrink-0 mb-4">
        <h1 className="text-2xl font-semibold">Chat</h1>
        <p className="text-sm text-zinc-500 mt-1">
          Interact with the agent directly
        </p>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto rounded-xl border border-zinc-800 bg-zinc-900/50 p-4 space-y-4">
        {loadingHistory ? (
          <div className="flex items-center justify-center py-12 text-zinc-500 text-sm">
            Loading history...
          </div>
        ) : messages.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-zinc-600">
            <Bot className="h-10 w-10 mb-3" />
            <p className="text-sm">No messages yet. Start a conversation.</p>
          </div>
        ) : (
          messages.map((msg, i) => (
            <div
              key={i}
              className={clsx(
                "flex gap-3",
                msg.role === "user" ? "justify-end" : "justify-start"
              )}
            >
              {msg.role !== "user" && (
                <div className="flex-shrink-0 mt-0.5">
                  <div className="h-7 w-7 rounded-full bg-blue-600/20 flex items-center justify-center">
                    <Bot className="h-3.5 w-3.5 text-blue-400" />
                  </div>
                </div>
              )}
              <div
                className={clsx(
                  "max-w-[75%] rounded-2xl px-4 py-2.5 text-sm",
                  msg.role === "user"
                    ? "bg-blue-600 text-white rounded-br-md"
                    : "bg-zinc-800 text-zinc-200 rounded-bl-md"
                )}
              >
                <p className="whitespace-pre-wrap break-words">
                  {msg.content}
                </p>
              </div>
              {msg.role === "user" && (
                <div className="flex-shrink-0 mt-0.5">
                  <div className="h-7 w-7 rounded-full bg-zinc-700 flex items-center justify-center">
                    <User className="h-3.5 w-3.5 text-zinc-300" />
                  </div>
                </div>
              )}
            </div>
          ))
        )}

        {sending && (
          <div className="flex gap-3 justify-start">
            <div className="flex-shrink-0 mt-0.5">
              <div className="h-7 w-7 rounded-full bg-blue-600/20 flex items-center justify-center">
                <Bot className="h-3.5 w-3.5 text-blue-400" />
              </div>
            </div>
            <div className="bg-zinc-800 rounded-2xl rounded-bl-md px-4 py-3">
              <Loader2 className="h-4 w-4 text-zinc-500 animate-spin" />
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="flex-shrink-0 mt-3">
        <div className="flex gap-2">
          <input
            ref={inputRef}
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
              }
            }}
            placeholder="Type a message..."
            className="input flex-1"
            disabled={sending}
          />
          <button
            onClick={sendMessage}
            disabled={sending || !input.trim()}
            className={clsx(
              "btn-primary px-4",
              (sending || !input.trim()) && "opacity-50 cursor-not-allowed"
            )}
          >
            <Send className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
