import type { ChatMessage } from "./types";

const AGENT_URL = process.env.NEXT_PUBLIC_AGENT_URL ?? "http://localhost:8000";

export interface ChatResponse {
  response: string;
  prediction_id: string | null;
  annotated_image: string | null;
  agent_loop_time_s: number;
  iterations: number;
  tools_called: string[];
  context_limit_exceeded: boolean;
}

export async function sendMessage(messages: ChatMessage[]): Promise<ChatResponse> {
  const res = await fetch(`${AGENT_URL}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages }),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(text || res.statusText);
  }
  const data = await res.json();
  return data as ChatResponse;
}
