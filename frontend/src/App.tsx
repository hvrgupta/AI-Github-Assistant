import { useState } from "react";
import { Header } from "./components/Header";
import { ChatWindow } from "./components/ChatWindow";
import { ChatInput } from "./components/ChatInput";
import { sendAgentMessage } from "./services/api";
import type { ChatMessage } from "./types/chat";

function createMessageId(): string {
  return crypto.randomUUID();
}

export default function App() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [threadId, setThreadId] = useState<string | undefined>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const startNewChat = () => {
    if (loading) {
      return;
    }
    setMessages([]);
    setThreadId(undefined);
    setError(null);
  };

  const sendMessage = async (content: string) => {
    setError(null);
    setMessages((current) => [
      ...current,
      { id: createMessageId(), role: "user", content },
    ]);
    setLoading(true);

    try {
      const response = await sendAgentMessage({
        message: content,
        ...(threadId ? { thread_id: threadId } : {}),
      });
      setThreadId(response.thread_id);
      setMessages((current) => [
        ...current,
        {
          id: createMessageId(),
          role: "assistant",
          content: response.response,
        },
      ]);
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "The assistant request failed.",
      );
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="app-shell">
      <Header onNewChat={startNewChat} disabled={loading} />
      <ChatWindow messages={messages} loading={loading} error={error} />
      <ChatInput onSend={sendMessage} disabled={loading} />
    </div>
  );
}
