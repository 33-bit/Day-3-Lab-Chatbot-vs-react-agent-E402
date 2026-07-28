import type { AgentMode, AppConfig, ChatResponse } from "./types";

const API_BASE = (import.meta.env.VITE_API_URL || "/api").replace(/\/$/, "");

async function parseResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let message = `Yêu cầu thất bại với mã ${response.status}.`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) message = body.detail;
    } catch {
      // Keep the status-based fallback when the server does not return JSON.
    }
    throw new Error(message);
  }
  return (await response.json()) as T;
}

export async function getConfig(): Promise<AppConfig> {
  return parseResponse<AppConfig>(
    await fetch(`${API_BASE}/config`, { headers: { Accept: "application/json" } }),
  );
}

export async function sendChatMessage(
  message: string,
  mode: AgentMode,
  provider: string,
  confirmActions = false,
): Promise<ChatResponse> {
  return parseResponse<ChatResponse>(
    await fetch(`${API_BASE}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, mode, provider, confirm_actions: confirmActions }),
    }),
  );
}
