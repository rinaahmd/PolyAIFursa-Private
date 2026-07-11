import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const CHAT_ID_STORAGE_KEY = "polyai-chat-id";

/**
 * crypto.randomUUID() only exists in "secure contexts" (HTTPS or localhost)
 * - this app is served over plain http://<ec2-ip>:3000, which the browser
 * does NOT treat as secure, so crypto.randomUUID is undefined there even
 * though it works fine in local dev (localhost is exempt). This fallback
 * has no such restriction: it just needs to be unique enough per browser
 * tab, not cryptographically unpredictable.
 */
function generateId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

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
    id = generateId();
    window.sessionStorage.setItem(CHAT_ID_STORAGE_KEY, id);
  }
  return id;
}
