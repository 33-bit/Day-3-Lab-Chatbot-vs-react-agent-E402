import { useEffect, useMemo, useRef, useState } from "react";
import { IconButton, Select, Tooltip } from "@radix-ui/themes";
import {
  ArrowCounterClockwise,
  CaretDown,
  ChatCircleText,
  CheckCircle,
  FlowArrow,
  List,
  Moon,
  PaperPlaneRight,
  Plus,
  SidebarSimple,
  Sun,
  WarningCircle,
  Wrench,
  X,
} from "@phosphor-icons/react";

import { getConfig, sendChatMessage } from "./api";
import type { AgentMode, AppConfig, ConversationMessage, TraceEvent } from "./types";

const FALLBACK_PROMPTS = [
  "Bạn có thể hỗ trợ tôi những gì?",
  "Chính sách đổi trả của cửa hàng như thế nào?",
  "Tôi muốn kiểm tra đơn hàng DH001.",
];

function makeId(prefix: string) {
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function getInitialTheme(): "light" | "dark" {
  const stored = window.localStorage.getItem("order-agent-theme");
  if (stored === "light" || stored === "dark") return stored;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function modeLabel(mode: AgentMode) {
  return mode === "baseline" ? "Baseline Chatbot" : "ReAct Agent";
}

export default function App() {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [configError, setConfigError] = useState("");
  const [mode, setMode] = useState<AgentMode>("baseline");
  const [providerId, setProviderId] = useState("mock");
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [trace, setTrace] = useState<TraceEvent[]>([]);
  const [draft, setDraft] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [traceOpen, setTraceOpen] = useState(false);
  const [confirmActions, setConfirmActions] = useState(false);
  const [theme, setTheme] = useState<"light" | "dark">(getInitialTheme);
  const conversationRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let ignore = false;
    getConfig()
      .then((payload) => {
        if (ignore) return;
        setConfig(payload);
        setProviderId(payload.active_provider.id);
      })
      .catch((error: Error) => {
        if (!ignore) setConfigError(error.message);
      });
    return () => {
      ignore = true;
    };
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    window.localStorage.setItem("order-agent-theme", theme);
  }, [theme]);

  useEffect(() => {
    const node = conversationRef.current;
    if (node) node.scrollTo({ top: node.scrollHeight, behavior: "smooth" });
  }, [messages, isSending]);

  const selectedProvider = useMemo(
    () => config?.providers.find((provider) => provider.id === providerId),
    [config, providerId],
  );

  const prompts = useMemo(() => {
    if (!config?.examples.length) return FALLBACK_PROMPTS;
    return config.examples.slice(0, 3).map((example) => example.question);
  }, [config]);

  const backendLabel = config
    ? "Backend sẵn sàng"
    : configError
      ? "Backend chưa kết nối"
      : "Đang kết nối backend";

  function resetConversation() {
    setMessages([]);
    setTrace([]);
    setDraft("");
    setConfirmActions(false);
    setSidebarOpen(false);
  }

  function changeMode(nextMode: AgentMode) {
    setMode(nextMode);
    setTrace([]);
    setConfirmActions(false);
    setSidebarOpen(false);
  }

  async function submitMessage(value?: string) {
    const message = (value ?? draft).trim();
    if (!message || isSending) return;

    setMessages((current) => [
      ...current,
      { id: makeId("user"), role: "user", content: message },
    ]);
    setDraft("");
    setTrace([]);
    setIsSending(true);

    try {
      const response = await sendChatMessage(
        message,
        mode,
        providerId,
        mode === "react" && confirmActions,
      );
      setMessages((current) => [
        ...current,
        {
          id: response.id,
          role: "assistant",
          content: response.content,
          status: response.status,
          provider: response.provider,
        },
      ]);
      setTrace(response.trace);
    } catch (error) {
      const text = error instanceof Error ? error.message : "Không thể kết nối tới backend.";
      setMessages((current) => [
        ...current,
        { id: makeId("error"), role: "assistant", content: text, status: "error" },
      ]);
      setTrace([{ kind: "system", label: "Kết nối thất bại", detail: text, status: "error" }]);
    } finally {
      setIsSending(false);
      if (mode === "react") setConfirmActions(false);
    }
  }

  return (
    <div className="app-shell">
      <button
        className={`mobile-scrim ${sidebarOpen ? "visible" : ""}`}
        aria-label="Đóng thanh điều hướng"
        onClick={() => setSidebarOpen(false)}
      />

      <aside className={`sidebar ${sidebarOpen ? "mobile-open" : ""}`}>
        <div className="brand-row">
          <div className="brand-mark" aria-hidden="true">OA</div>
          <div>
            <p className="brand-name">Order Agent</p>
            <p className="brand-note">Hỗ trợ đơn hàng</p>
          </div>
          <IconButton className="mobile-close" variant="ghost" color="gray" aria-label="Đóng thanh điều hướng" onClick={() => setSidebarOpen(false)}>
            <X size={18} />
          </IconButton>
        </div>

        <button className="new-chat-button" onClick={resetConversation}>
          <Plus size={17} weight="bold" />
          Cuộc trò chuyện mới
        </button>

        <nav className="mode-nav" aria-label="Chọn chế độ hội thoại">
          <p className="nav-heading">Chế độ</p>
          <button className={`mode-button ${mode === "baseline" ? "active" : ""}`} onClick={() => changeMode("baseline")}>
            <ChatCircleText size={19} />
            <span>
              <strong>Baseline Chatbot</strong>
              <small>Trả lời trực tiếp từ model</small>
            </span>
          </button>
          <button className={`mode-button ${mode === "react" ? "active" : ""}`} onClick={() => changeMode("react")}>
            <FlowArrow size={19} />
            <span>
              <strong>ReAct Agent</strong>
              <small>Dùng tool và guardrail</small>
            </span>
          </button>
        </nav>

        <div className="sidebar-prompts">
          <p className="nav-heading">Gợi ý nhanh</p>
          {prompts.map((prompt) => (
            <button key={prompt} onClick={() => submitMessage(prompt)}>{prompt}</button>
          ))}
        </div>

        <div className="sidebar-footer">
          <span className={`connection-dot ${configError ? "error" : ""}`} aria-hidden="true" />
          {backendLabel}
        </div>
      </aside>

      <main className="workspace">
        <header className="topbar">
          <div className="topbar-left">
            <IconButton className="mobile-menu" variant="ghost" color="gray" aria-label="Mở thanh điều hướng" onClick={() => setSidebarOpen(true)}>
              <List size={20} />
            </IconButton>
            <div>
              <p className="conversation-title">Hỗ trợ đơn hàng</p>
              <p className="conversation-mode">{modeLabel(mode)}</p>
            </div>
          </div>

          <div className="topbar-actions">
            {config ? (
              <Select.Root value={providerId} onValueChange={setProviderId}>
                <Select.Trigger className="provider-trigger" aria-label="Chọn model provider" />
                <Select.Content>
                  {config.providers.map((provider) => (
                    <Select.Item key={provider.id} value={provider.id}>
                      {provider.label}{provider.configured ? "" : " (chưa cấu hình)"}
                    </Select.Item>
                  ))}
                </Select.Content>
              </Select.Root>
            ) : (
              <span className="provider-loading">Đang tải provider</span>
            )}

            <Tooltip content={theme === "light" ? "Dùng nền tối" : "Dùng nền sáng"}>
              <IconButton variant="ghost" color="gray" aria-label={theme === "light" ? "Dùng nền tối" : "Dùng nền sáng"} onClick={() => setTheme((current) => current === "light" ? "dark" : "light")}>
                {theme === "light" ? <Moon size={19} /> : <Sun size={19} />}
              </IconButton>
            </Tooltip>

            <Tooltip content="Xem chi tiết phiên">
              <IconButton className="trace-mobile-button" variant="ghost" color="gray" aria-label="Xem chi tiết phiên" onClick={() => setTraceOpen(true)}>
                <SidebarSimple size={20} />
              </IconButton>
            </Tooltip>
          </div>
        </header>

        <section className="conversation" ref={conversationRef} aria-live="polite">
          {messages.length === 0 ? (
            <div className="empty-state">
              <div className="empty-monogram" aria-hidden="true">OA</div>
              <h1>Bạn cần hỗ trợ đơn hàng nào?</h1>
              <p>Hỏi về chính sách đổi trả hoặc thử một tình huống từ bài lab.</p>

              {mode === "react" && (
                <div className="agent-notice react-ready-notice">
                  <CheckCircle size={20} weight="fill" />
                  <span>ReAct Agent có thể tra cứu đơn hàng bằng tool thật và hiển thị trace đã kiểm chứng.</span>
                </div>
              )}

              <div className="prompt-grid">
                {prompts.map((prompt) => (
                  <button key={prompt} onClick={() => submitMessage(prompt)}>
                    <span>{prompt}</span>
                    <PaperPlaneRight size={16} />
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="message-list">
              {messages.map((message) => (
                <article key={message.id} className={`message ${message.role} ${message.status ?? ""}`}>
                  <div className="message-meta">
                    {message.role === "assistant" ? <span className="assistant-avatar">OA</span> : <span className="user-avatar">Bạn</span>}
                    <strong>{message.role === "assistant" ? "Order Agent" : "Bạn"}</strong>
                    {message.status === "error" && <span className="message-status error">Cần kiểm tra</span>}
                  </div>
                  <p>{message.content}</p>
                  {message.provider && <span className="message-provider">{message.provider.label} / {message.provider.model}</span>}
                </article>
              ))}

              {isSending && (
                <article className="message assistant loading-message">
                  <div className="message-meta"><span className="assistant-avatar">OA</span><strong>Order Agent</strong></div>
                  <div className="typing-lines" aria-label="Order Agent đang trả lời"><span /><span /><span /></div>
                </article>
              )}
            </div>
          )}
        </section>

        <div className="composer-wrap">
          {mode === "react" && (
            <label className="action-confirmation">
              <input
                type="checkbox"
                checked={confirmActions}
                onChange={(event) => setConfirmActions(event.target.checked)}
              />
              <span>
                <strong>Cho phép tạo yêu cầu đổi/trả</strong>
                <small>Chỉ áp dụng cho lượt gửi tiếp theo.</small>
              </span>
            </label>
          )}
          <form className="composer" onSubmit={(event) => { event.preventDefault(); submitMessage(); }}>
            <label htmlFor="message-input" className="sr-only">Tin nhắn của bạn</label>
            <textarea
              id="message-input"
              value={draft}
              rows={1}
              maxLength={4000}
              placeholder={mode === "baseline" ? "Nhắn cho Baseline Chatbot" : "Thử giao việc cho ReAct Agent"}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                  event.preventDefault();
                  submitMessage();
                }
              }}
            />
            <button className="send-button" type="submit" aria-label="Gửi tin nhắn" disabled={!draft.trim() || isSending}>
              <PaperPlaneRight size={18} weight="bold" />
            </button>
          </form>
          <p className="composer-note">
            {mode === "baseline" ? "Baseline trả lời trực tiếp từ model và không gọi công cụ." : "ReAct có thể gọi tool; Thought thô được ẩn khỏi giao diện."}
          </p>
        </div>
      </main>

      <aside className={`trace-panel ${traceOpen ? "mobile-open" : ""}`}>
        <div className="trace-header">
          <div><p className="trace-title">Chi tiết phiên</p><p className="trace-subtitle">Trạng thái thực thi hiện tại</p></div>
          <IconButton className="trace-close-button" variant="ghost" color="gray" aria-label="Đóng chi tiết phiên" onClick={() => setTraceOpen(false)}><X size={18} /></IconButton>
        </div>

        <section className="trace-section">
          <p className="trace-heading">Phiên làm việc</p>
          <dl className="session-details">
            <div><dt>Chế độ</dt><dd>{modeLabel(mode)}</dd></div>
            <div><dt>Provider</dt><dd>{selectedProvider?.label ?? "Đang tải"}</dd></div>
            <div><dt>Model</dt><dd>{selectedProvider?.model ?? "Chưa xác định"}</dd></div>
          </dl>
          {selectedProvider && !selectedProvider.configured && (
            <div className="provider-warning">
              <WarningCircle size={18} />
              <span>{selectedProvider.id === "mock" ? "Mock chạy offline và không cần API key." : "Provider này chưa có API key trong backend."}</span>
            </div>
          )}
        </section>

        <section className="trace-section">
          <div className="trace-section-title">
            <p className="trace-heading">Trace</p>
            {trace.length > 0 && <button onClick={() => setTrace([])} aria-label="Xóa trace"><ArrowCounterClockwise size={15} />Xóa</button>}
          </div>
          {trace.length === 0 ? (
            <p className="trace-empty">Trace sẽ xuất hiện sau khi backend xử lý một tin nhắn.</p>
          ) : (
            <div className="trace-list">
              {trace.map((event, index) => (
                <div className={`trace-event ${event.status}`} key={`${event.label}-${index}`}>
                  {event.status === "completed" ? <CheckCircle size={18} weight="fill" /> : <WarningCircle size={18} />}
                  <div><strong>{event.label}</strong><p>{event.detail}</p></div>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="trace-section tools-section">
          <div className="tools-title"><Wrench size={17} /><p className="trace-heading">Tool registry</p></div>
          {config?.tools.map((tool) => (
            <details key={tool.name}>
              <summary><code>{tool.name}</code><CaretDown size={15} /></summary>
              <p>{tool.description}</p>
              <span>Tham số: {tool.parameters.join(", ")}</span>
            </details>
          )) ?? <p className="trace-empty">Đang tải danh sách tool.</p>}
          {mode === "baseline" && <p className="tool-mode-note">Các tool bị khóa trong Baseline Chatbot.</p>}
        </section>
      </aside>
    </div>
  );
}
