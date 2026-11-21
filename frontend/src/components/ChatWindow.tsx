import { useEffect, useRef } from "react";
import type { ChatMessage } from "../types/chat";
import { MessageBubble } from "./MessageBubble";

interface ChatWindowProps {
  messages: ChatMessage[];
  loading: boolean;
  error: string | null;
}

export function ChatWindow({ messages, loading, error }: ChatWindowProps) {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading, error]);

  return (
    <main className="chat-window" aria-live="polite">
      {messages.length === 0 && (
        <section className="empty-state">
          <h1>Developer knowledge, grounded in your sources</h1>
          <p>
            Ask about GitHub repositories or the documentation in the company
            knowledge directory.
          </p>
        </section>
      )}

      {messages.map((message) => (
        <MessageBubble key={message.id} message={message} />
      ))}

      {loading && (
        <article className="message-row">
          <div className="message-bubble message-assistant loading-bubble">
            <span className="loading-dot" />
            <span className="loading-dot" />
            <span className="loading-dot" />
            <span className="sr-only">Assistant is responding</span>
          </div>
        </article>
      )}

      {error && (
        <div className="error-banner" role="alert">
          {error}
        </div>
      )}
      <div ref={endRef} />
    </main>
  );
}
