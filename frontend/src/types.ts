export type AgentMode = "baseline" | "react";
export type ResponseStatus = "completed" | "error";

export interface ProviderInfo {
  id: string;
  label: string;
  model: string;
  configured: boolean;
}

export interface ToolInfo {
  name: string;
  description: string;
  side_effect: string;
  parameters: string[];
}

export interface ExamplePrompt {
  id: number;
  category: string;
  question: string;
}

export interface TraceEvent {
  kind: "system" | "model" | "tool";
  label: string;
  detail: string;
  status: "pending" | "completed" | "error";
}

export interface AppConfig {
  product_name: string;
  active_provider: ProviderInfo;
  providers: ProviderInfo[];
  tools: ToolInfo[];
  examples: ExamplePrompt[];
}

export interface ChatResponse {
  id: string;
  mode: AgentMode;
  status: ResponseStatus;
  content: string;
  provider: ProviderInfo;
  trace: TraceEvent[];
}

export interface ConversationMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  status?: ResponseStatus;
  provider?: ProviderInfo;
}
