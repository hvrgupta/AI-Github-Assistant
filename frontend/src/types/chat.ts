export type ChatRole = "user" | "assistant";

export interface ChatMessage {
  id: string;
  role: ChatRole;
  content: string;
}

export interface AgentRequest {
  message: string;
  thread_id?: string;
}

export interface AgentResponse {
  request_id: string;
  thread_id: string;
  status: "completed";
  response: string;
}
