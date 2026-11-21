import type { AgentRequest, AgentResponse } from "../types/chat";

async function readErrorMessage(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (
      typeof body === "object"
      && body !== null
      && "detail" in body
      && typeof body.detail === "string"
    ) {
      return body.detail;
    }
  } catch {
    // Fall through to the status-based message.
  }
  return `Request failed with status ${response.status}.`;
}

export async function sendAgentMessage(
  request: AgentRequest,
): Promise<AgentResponse> {
  let response: Response;
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), 180_000);
  try {
    response = await fetch("/api/v1/agent", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      credentials: "same-origin",
      signal: controller.signal,
      body: JSON.stringify(request),
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new Error("The assistant response timed out. Please try again.");
    }
    if (error instanceof TypeError) {
      throw new Error("Unable to reach the assistant. Check your connection.");
    }
    throw error;
  } finally {
    window.clearTimeout(timeoutId);
  }

  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new Error("The assistant returned an invalid response.");
  }

  if (
    typeof body !== "object"
    || body === null
    || !("request_id" in body)
    || typeof body.request_id !== "string"
    || !("thread_id" in body)
    || typeof body.thread_id !== "string"
    || !("status" in body)
    || body.status !== "completed"
    || !("response" in body)
    || typeof body.response !== "string"
  ) {
    throw new Error("The assistant returned an unexpected response.");
  }

  return body as AgentResponse;
}
