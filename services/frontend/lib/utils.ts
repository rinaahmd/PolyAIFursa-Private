import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const CHAT_ID_STORAGE_KEY = "polyai-chat-id";

/**
 * A stable ID for this browser tab's conversation, generated once and kept
 * in sessionStorage. The agent uses this to key its per-chat state (S3
 * scratch keys, in-memory detection cache) - without a real per-session ID,
 * every tab/user sent requests under the same literal "chat" key server-side,
 * so concurrent users could silently overwrite each other's in-progress
 * image edits and object detections.
 */
export function getChatId(): string {
  if (typeof window === "undefined") return "server";
  let id = window.sessionStorage.getItem(CHAT_ID_STORAGE_KEY);
  if (!id) {
    id = crypto.randomUUID();
    window.sessionStorage.setItem(CHAT_ID_STORAGE_KEY, id);
  }
  return id;
}
