"use strict";

const API_BASE = "/api/v1";

const state = {
  sessions: [],
  activeSessionId: null,
  messages: [],
  isGenerating: false,
  stopRequested: false,
  abortController: null,
  errorTimeout: null,
};

class ApiError extends Error {
  constructor(status, detail) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
  }
}

const elements = {
  sidebar: document.getElementById("sidebar"),
  sidebarBackdrop: document.getElementById("sidebarBackdrop"),
  mobileMenuButton: document.getElementById("mobileMenuButton"),

  healthIndicator: document.getElementById("healthIndicator"),
  healthText: document.getElementById("healthText"),

  newSessionButton: document.getElementById("newSessionButton"),
  deleteSessionButton: document.getElementById(
    "deleteSessionButton",
  ),
  sessionList: document.getElementById("sessionList"),

  conversationTitle: document.getElementById(
    "conversationTitle",
  ),
  conversationStatus: document.getElementById(
    "conversationStatus",
  ),

  errorBanner: document.getElementById("errorBanner"),
  errorText: document.getElementById("errorText"),
  dismissErrorButton: document.getElementById(
    "dismissErrorButton",
  ),

  messageList: document.getElementById("messageList"),
  emptyState: document.getElementById("emptyState"),

  composerForm: document.getElementById("composerForm"),
  messageInput: document.getElementById("messageInput"),
  characterCount: document.getElementById("characterCount"),
  sendButton: document.getElementById("sendButton"),
  stopButton: document.getElementById("stopButton"),
};

document.addEventListener("DOMContentLoaded", initialize);

async function initialize() {
  bindEvents();
  updateCharacterCount();
  updateControls();

  const healthy = await checkHealth();

  if (!healthy) {
    showError(
      "无法连接 Agent 后端。请确认 Uvicorn 服务器已经启动。",
    );
    return;
  }

  try {
    await refreshSessions();

    if (state.sessions.length === 0) {
      await createNewSession();
    } else {
      await activateSession(state.sessions[0].session_id);
    }
  } catch (error) {
    handleError(error);
  }

  window.setInterval(() => {
    void checkHealth();
  }, 30000);
}

function bindEvents() {
  elements.composerForm.addEventListener(
    "submit",
    handleMessageSubmit,
  );

  elements.messageInput.addEventListener(
    "input",
    () => {
      updateCharacterCount();
      resizeMessageInput();
      updateControls();
    },
  );

  elements.messageInput.addEventListener(
    "keydown",
    (event) => {
      if (
        event.key === "Enter"
        && !event.shiftKey
        && !event.isComposing
      ) {
        event.preventDefault();
        elements.composerForm.requestSubmit();
      }
    },
  );

  elements.newSessionButton.addEventListener(
    "click",
    () => {
      void createNewSession();
    },
  );

  elements.deleteSessionButton.addEventListener(
    "click",
    () => {
      void deleteActiveSession();
    },
  );

  elements.stopButton.addEventListener(
    "click",
    () => {
      void stopGeneration();
    },
  );

  elements.dismissErrorButton.addEventListener(
    "click",
    clearError,
  );

  elements.mobileMenuButton.addEventListener(
    "click",
    toggleSidebar,
  );

  elements.sidebarBackdrop.addEventListener(
    "click",
    closeSidebar,
  );

  document.querySelectorAll(".suggestion-button").forEach(
    (button) => {
      button.addEventListener("click", () => {
        if (state.isGenerating) {
          return;
        }

        const prompt = button.dataset.prompt || "";
        elements.messageInput.value = prompt;
        updateCharacterCount();
        resizeMessageInput();
        elements.messageInput.focus();
      });
    },
  );
}

async function checkHealth() {
  try {
    const payload = await requestJson("/health");

    const healthy = (
      payload.status === "ok"
      && payload.agent_service_started === true
    );

    elements.healthIndicator.dataset.state = (
      healthy ? "healthy" : "error"
    );

    elements.healthText.textContent = (
      healthy ? "服务运行正常" : "Agent 服务不可用"
    );

    return healthy;
  } catch {
    elements.healthIndicator.dataset.state = "error";
    elements.healthText.textContent = "无法连接服务";
    return false;
  }
}

async function refreshSessions() {
  const payload = await requestJson(
    `${API_BASE}/sessions`,
  );

  state.sessions = [...payload.sessions].sort(
    (left, right) => (
      new Date(right.created_at).getTime()
      - new Date(left.created_at).getTime()
    ),
  );

  renderSessions();
  updateHeader();
}

async function createNewSession() {
  if (state.isGenerating) {
    showError("请先停止当前回答，再创建新对话。");
    return;
  }

  clearError();
  setPageBusy(true);

  try {
    const payload = await requestJson(
      `${API_BASE}/sessions`,
      {
        method: "POST",
        body: {
          name: `新对话 ${state.sessions.length + 1}`,
        },
      },
    );

    await refreshSessions();

    state.activeSessionId = payload.session_id;
    state.messages = [];

    renderSessions();
    renderMessages();
    updateHeader();
    updateControls();

    closeSidebar();
    elements.messageInput.focus();
  } catch (error) {
    handleError(error);
  } finally {
    setPageBusy(false);
  }
}

async function activateSession(sessionId) {
  if (state.isGenerating) {
    showError("生成回答时不能切换对话，请先停止生成。");
    return;
  }

  clearError();
  state.activeSessionId = sessionId;
  state.messages = [];

  renderSessions();
  renderMessages();
  updateHeader();
  updateControls();
  closeSidebar();

  try {
    const payload = await requestJson(
      `${API_BASE}/sessions/${sessionId}/history`,
    );

    state.messages = normalizeHistoryMessages(
      payload.history,
    );

    renderMessages();
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      await recoverFromMissingSession();
      return;
    }

    handleError(error);
  }

  elements.messageInput.focus();
}

async function deleteActiveSession() {
  const sessionId = state.activeSessionId;

  if (!sessionId) {
    return;
  }

  if (state.isGenerating) {
    showError("请先停止当前回答，再删除对话。");
    return;
  }

  const session = findSession(sessionId);
  const sessionName = session?.name || "当前对话";

  const confirmed = window.confirm(
    `确定删除“${sessionName}”吗？\n该操作无法撤销。`,
  );

  if (!confirmed) {
    return;
  }

  clearError();
  setPageBusy(true);

  try {
    await requestJson(
      `${API_BASE}/sessions/${sessionId}`,
      {
        method: "DELETE",
      },
    );

    state.activeSessionId = null;
    state.messages = [];

    await refreshSessions();

    if (state.sessions.length > 0) {
      await activateSession(
        state.sessions[0].session_id,
      );
    } else {
      await createNewSession();
    }
  } catch (error) {
    handleError(error);
  } finally {
    setPageBusy(false);
  }
}

async function handleMessageSubmit(event) {
  event.preventDefault();

  if (state.isGenerating) {
    return;
  }

  const content = elements.messageInput.value.trim();

  if (!content) {
    showError("请输入消息内容。");
    elements.messageInput.focus();
    return;
  }

  if (!state.activeSessionId) {
    await createNewSession();

    if (!state.activeSessionId) {
      return;
    }
  }

  clearError();

  const sessionId = state.activeSessionId;
  const userMessage = createMessage("user", content);
  const assistantMessage = createMessage(
    "assistant",
    "",
    {
      pending: true,
    },
  );

  state.messages.push(userMessage, assistantMessage);

  appendMessageElement(userMessage);
  appendMessageElement(assistantMessage);

  elements.messageInput.value = "";
  updateCharacterCount();
  resizeMessageInput();

  state.isGenerating = true;
  state.stopRequested = false;
  state.abortController = new AbortController();

  updateControls();
  updateHeader();
  scrollToBottom();

  try {
    const response = await fetch(
      (
        `${API_BASE}/sessions/${sessionId}`
        + "/messages/stream"
      ),
      {
        method: "POST",
        headers: {
          "Accept": "text/event-stream",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          content,
        }),
        signal: state.abortController.signal,
      },
    );

    if (!response.ok) {
      throw await responseToApiError(response);
    }

    if (!response.body) {
      throw new Error(
        "浏览器没有收到可读取的流式响应。",
      );
    }

    await consumeSseStream(
      response.body,
      (eventName, payload) => {
        if (eventName === "token") {
          const text = String(payload.text || "");

          if (text) {
            assistantMessage.content += text;
            assistantMessage.pending = false;
            updateMessageElement(assistantMessage);
            scrollToBottom();
          }

          return;
        }

        if (eventName === "done") {
          assistantMessage.pending = false;
          updateMessageElement(assistantMessage);
          return;
        }

        if (eventName === "error") {
          throw new ApiError(
            500,
            payload.detail || "流式回答失败。",
          );
        }
      },
    );

    if (
      !assistantMessage.content
      && !state.stopRequested
    ) {
      assistantMessage.content = "没有收到可显示的回答。";
    }
  } catch (error) {
    if (
      error instanceof DOMException
      && error.name === "AbortError"
      && state.stopRequested
    ) {
      assistantMessage.stopped = true;

      if (!assistantMessage.content) {
        assistantMessage.content = "已停止生成。";
      }
    } else {
      assistantMessage.failed = true;

      if (!assistantMessage.content) {
        assistantMessage.content = (
          "回答生成失败，请稍后重试。"
        );
      }

      handleError(error);
    }
  } finally {
    assistantMessage.pending = false;
    updateMessageElement(assistantMessage);

    state.isGenerating = false;
    state.abortController = null;

    updateControls();
    updateHeader();

    await waitForSessionIdle(sessionId);
    await refreshSessionsSafely();

    elements.messageInput.focus();
  }
}

async function stopGeneration() {
  if (
    !state.isGenerating
    || !state.activeSessionId
  ) {
    return;
  }

  state.stopRequested = true;
  elements.stopButton.disabled = true;
  elements.conversationStatus.textContent = "正在停止";

  const sessionId = state.activeSessionId;

  const interruptRequest = requestJson(
    `${API_BASE}/sessions/${sessionId}/interrupt`,
    {
      method: "POST",
    },
  );

  state.abortController?.abort();

  try {
    await interruptRequest;
  } catch (error) {
    if (
      !(error instanceof ApiError)
      || error.status !== 404
    ) {
      console.warn(
        "Interrupt request failed:",
        error,
      );
    }
  }
}

async function consumeSseStream(
  readableStream,
  onEvent,
) {
  const reader = readableStream.getReader();
  const decoder = new TextDecoder("utf-8");

  let buffer = "";

  try {
    while (true) {
      const { value, done } = await reader.read();

      if (value) {
        buffer += decoder.decode(
          value,
          {
            stream: true,
          },
        );
      }

      buffer = buffer
        .replaceAll("\r\n", "\n")
        .replaceAll("\r", "\n");

      let boundaryIndex = buffer.indexOf("\n\n");

      while (boundaryIndex !== -1) {
        const block = buffer.slice(0, boundaryIndex);
        buffer = buffer.slice(boundaryIndex + 2);

        if (block.trim()) {
          const event = parseSseBlock(block);
          onEvent(event.name, event.data);
        }

        boundaryIndex = buffer.indexOf("\n\n");
      }

      if (done) {
        buffer += decoder.decode();
        break;
      }
    }

    if (buffer.trim()) {
      const event = parseSseBlock(buffer);
      onEvent(event.name, event.data);
    }
  } finally {
    reader.releaseLock();
  }
}

function parseSseBlock(block) {
  let eventName = "message";
  const dataLines = [];

  for (const line of block.split("\n")) {
    if (!line || line.startsWith(":")) {
      continue;
    }

    const separatorIndex = line.indexOf(":");

    const field = (
      separatorIndex === -1
        ? line
        : line.slice(0, separatorIndex)
    );

    let value = (
      separatorIndex === -1
        ? ""
        : line.slice(separatorIndex + 1)
    );

    if (value.startsWith(" ")) {
      value = value.slice(1);
    }

    if (field === "event") {
      eventName = value;
    } else if (field === "data") {
      dataLines.push(value);
    }
  }

  const rawData = dataLines.join("\n");

  let data = {};

  if (rawData) {
    try {
      data = JSON.parse(rawData);
    } catch {
      data = {
        text: rawData,
      };
    }
  }

  return {
    name: eventName,
    data,
  };
}

function normalizeHistoryMessages(history) {
  const rawMessages = (
    Array.isArray(history?.messages)
      ? history.messages
      : []
  );

  const normalized = [];

  for (const rawMessage of rawMessages) {
    const role = normalizeRole(rawMessage);

    if (!role) {
      continue;
    }

    const content = extractTextContent(rawMessage);

    if (!content.trim()) {
      continue;
    }

    normalized.push(
      createMessage(role, content),
    );
  }

  return normalized;
}

function normalizeRole(message) {
  const rawRole = String(
    message?.role
    ?? message?.sender
    ?? message?.type
    ?? message?.author
    ?? "",
  ).toLowerCase();

  if (
    rawRole.includes("user")
    || rawRole.includes("human")
  ) {
    return "user";
  }

  if (
    rawRole.includes("assistant")
    || rawRole.includes("agent")
    || rawRole.includes("model")
  ) {
    return "assistant";
  }

  return null;
}

function extractTextContent(value, depth = 0) {
  if (depth > 5 || value == null) {
    return "";
  }

  if (typeof value === "string") {
    return value;
  }

  if (Array.isArray(value)) {
    return value
      .map((item) => extractTextContent(item, depth + 1))
      .filter(Boolean)
      .join("");
  }

  if (typeof value === "object") {
    const candidateKeys = [
      "content",
      "text",
      "message",
      "value",
      "output_text",
    ];

    for (const key of candidateKeys) {
      if (key in value) {
        const extracted = extractTextContent(
          value[key],
          depth + 1,
        );

        if (extracted) {
          return extracted;
        }
      }
    }
  }

  return "";
}

function createMessage(
  role,
  content,
  options = {},
) {
  return {
    id: createLocalId(),
    role,
    content,
    pending: options.pending === true,
    stopped: options.stopped === true,
    failed: options.failed === true,
  };
}

function createLocalId() {
  if (
    window.crypto
    && typeof window.crypto.randomUUID === "function"
  ) {
    return window.crypto.randomUUID();
  }

  return (
    `${Date.now()}-`
    + Math.random().toString(16).slice(2)
  );
}

function renderMessages() {
  elements.messageList.replaceChildren();

  if (state.messages.length === 0) {
    elements.messageList.append(
      elements.emptyState,
    );
    return;
  }

  for (const message of state.messages) {
    elements.messageList.append(
      createMessageElement(message),
    );
  }

  scrollToBottom(false);
}

function appendMessageElement(message) {
  if (
    elements.emptyState.parentElement
    === elements.messageList
  ) {
    elements.emptyState.remove();
  }

  elements.messageList.append(
    createMessageElement(message),
  );
}

function updateMessageElement(message) {
  const existing = elements.messageList.querySelector(
    `[data-message-id="${message.id}"]`,
  );

  if (!existing) {
    return;
  }

  existing.replaceWith(
    createMessageElement(message),
  );
}

function createMessageElement(message) {
  const article = document.createElement("article");
  article.className = (
    `message message-${message.role}`
  );
  article.dataset.messageId = message.id;

  const avatar = document.createElement("div");
  avatar.className = "message-avatar";
  avatar.textContent = (
    message.role === "user" ? "你" : "K"
  );

  const body = document.createElement("div");
  body.className = "message-body";

  const role = document.createElement("div");
  role.className = "message-role";
  role.textContent = (
    message.role === "user"
      ? "你"
      : "Kohaku Public Agent"
  );

  const content = document.createElement("div");
  content.className = "message-content";

  if (message.pending && !message.content) {
    const placeholder = document.createElement("span");
    placeholder.className = "typing-placeholder";
    placeholder.textContent = "正在思考";
    content.append(placeholder);
  } else {
    renderSafeContent(content, message.content);
  }

  body.append(role, content);

  if (message.stopped || message.failed) {
    const statusElement = document.createElement("div");
    statusElement.className = "message-status";
    statusElement.textContent = (
      message.stopped
        ? "已停止生成"
        : "生成未完整完成"
    );
    body.append(statusElement);
  }

  article.append(avatar, body);
  return article;
}

function renderSafeContent(container, text) {
  container.replaceChildren();

  const sections = String(text).split("```");

  sections.forEach((section, index) => {
    if (index % 2 === 0) {
      if (!section) {
        return;
      }

      const textBlock = document.createElement("div");
      textBlock.className = "text-block";
      textBlock.textContent = section;
      container.append(textBlock);
      return;
    }

    let codeText = section;
    let language = "";

    const firstLineEnd = section.indexOf("\n");

    if (firstLineEnd !== -1) {
      const possibleLanguage = section
        .slice(0, firstLineEnd)
        .trim();

      if (
        /^[a-zA-Z0-9_+#.-]{1,20}$/.test(
          possibleLanguage,
        )
      ) {
        language = possibleLanguage;
        codeText = section.slice(firstLineEnd + 1);
      }
    }

    const pre = document.createElement("pre");
    pre.className = "code-block";

    if (language) {
      const languageLabel = document.createElement("div");
      languageLabel.className = "code-language";
      languageLabel.textContent = language;
      pre.append(languageLabel);
    }

    const code = document.createElement("code");
    code.textContent = codeText;
    pre.append(code);
    container.append(pre);
  });
}

function renderSessions() {
  elements.sessionList.replaceChildren();

  if (state.sessions.length === 0) {
    const empty = document.createElement("div");
    empty.className = "session-empty";
    empty.textContent = "还没有对话记录";
    elements.sessionList.append(empty);
    return;
  }

  for (const session of state.sessions) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "session-button";
    button.disabled = state.isGenerating;
    button.setAttribute(
      "aria-current",
      String(
        session.session_id === state.activeSessionId,
      ),
    );

    const icon = document.createElement("span");
    icon.className = "session-icon";
    icon.textContent = "✦";

    const copy = document.createElement("span");
    copy.className = "session-copy";

    const name = document.createElement("span");
    name.className = "session-name";
    name.textContent = session.name;

    const meta = document.createElement("span");
    meta.className = "session-meta";

    if (session.is_busy) {
      const busyDot = document.createElement("span");
      busyDot.className = "busy-dot";
      meta.append(busyDot);
    }

    const time = document.createElement("span");
    time.textContent = formatDate(session.created_at);
    meta.append(time);

    copy.append(name, meta);
    button.append(icon, copy);

    button.addEventListener("click", () => {
      void activateSession(session.session_id);
    });

    elements.sessionList.append(button);
  }
}

function updateHeader() {
  const session = findSession(
    state.activeSessionId,
  );

  elements.conversationTitle.textContent = (
    session?.name || "未选择对话"
  );

  if (state.isGenerating) {
    elements.conversationStatus.textContent = "正在生成";
  } else if (session?.is_busy) {
    elements.conversationStatus.textContent = "会话处理中";
  } else if (session) {
    elements.conversationStatus.textContent = "已连接";
  } else {
    elements.conversationStatus.textContent = "未连接会话";
  }
}

function updateControls() {
  const hasSession = Boolean(state.activeSessionId);
  const hasMessage = Boolean(
    elements.messageInput.value.trim(),
  );

  elements.newSessionButton.disabled = state.isGenerating;

  elements.deleteSessionButton.disabled = (
    !hasSession || state.isGenerating
  );

  elements.sendButton.disabled = (
    !hasSession
    || !hasMessage
    || state.isGenerating
  );

  elements.sendButton.hidden = state.isGenerating;
  elements.stopButton.hidden = !state.isGenerating;
  elements.stopButton.disabled = false;

  elements.messageInput.disabled = state.isGenerating;

  document
    .querySelectorAll(".suggestion-button")
    .forEach((button) => {
      button.disabled = state.isGenerating;
    });

  renderSessions();
}

function setPageBusy(busy) {
  elements.newSessionButton.disabled = busy;
  elements.deleteSessionButton.disabled = busy;
  elements.sendButton.disabled = busy;
}

function updateCharacterCount() {
  elements.characterCount.textContent = (
    `${elements.messageInput.value.length} / 50000`
  );
}

function resizeMessageInput() {
  elements.messageInput.style.height = "auto";

  elements.messageInput.style.height = (
    `${Math.min(
      elements.messageInput.scrollHeight,
      180,
    )}px`
  );
}

async function waitForSessionIdle(
  sessionId,
  attempts = 20,
) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      const session = await requestJson(
        `${API_BASE}/sessions/${sessionId}`,
      );

      if (!session.is_busy) {
        return;
      }
    } catch (error) {
      if (
        error instanceof ApiError
        && error.status === 404
      ) {
        return;
      }
    }

    await sleep(200);
  }
}

async function refreshSessionsSafely() {
  try {
    await refreshSessions();
  } catch (error) {
    console.warn(
      "Unable to refresh session metadata:",
      error,
    );
  }
}

async function recoverFromMissingSession() {
  state.activeSessionId = null;
  state.messages = [];

  await refreshSessions();

  if (state.sessions.length > 0) {
    await activateSession(
      state.sessions[0].session_id,
    );
  } else {
    await createNewSession();
  }
}

async function requestJson(
  path,
  options = {},
) {
  const headers = {
    "Accept": "application/json",
    ...(options.headers || {}),
  };

  let body;

  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }

  const response = await fetch(
    path,
    {
      method: options.method || "GET",
      headers,
      body,
      signal: options.signal,
    },
  );

  if (!response.ok) {
    throw await responseToApiError(response);
  }

  if (response.status === 204) {
    return null;
  }

  const rawText = await response.text();

  if (!rawText) {
    return {};
  }

  try {
    return JSON.parse(rawText);
  } catch {
    return {
      value: rawText,
    };
  }
}

async function responseToApiError(response) {
  let detail = `请求失败，状态码 ${response.status}`;

  try {
    const payload = await response.json();

    if (typeof payload.detail === "string") {
      detail = payload.detail;
    } else if (Array.isArray(payload.detail)) {
      detail = payload.detail
        .map((item) => item.msg || "数据验证失败")
        .join("；");
    }
  } catch {
    const text = await response.text();

    if (text) {
      detail = text;
    }
  }

  return new ApiError(response.status, detail);
}

function handleError(error) {
  console.error(error);

  if (error instanceof ApiError) {
    if (error.status === 404) {
      showError("该会话不存在或已经失效。");
      return;
    }

    if (error.status === 409) {
      showError(
        "该会话正在生成回答，请停止当前回答后再试。",
      );
      return;
    }

    if (error.status === 422) {
      showError(`输入内容无效：${error.message}`);
      return;
    }

    if (error.status === 503) {
      showError("Agent 服务暂时不可用。");
      return;
    }

    showError(error.message);
    return;
  }

  showError(
    error?.message
    || "发生未知错误，请检查服务器终端。",
  );
}

function showError(message) {
  clearTimeout(state.errorTimeout);

  elements.errorText.textContent = message;
  elements.errorBanner.hidden = false;

  state.errorTimeout = window.setTimeout(
    clearError,
    10000,
  );
}

function clearError() {
  clearTimeout(state.errorTimeout);
  state.errorTimeout = null;

  elements.errorText.textContent = "";
  elements.errorBanner.hidden = true;
}

function findSession(sessionId) {
  return state.sessions.find(
    (session) => session.session_id === sessionId,
  );
}

function formatDate(value) {
  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return "";
  }

  return new Intl.DateTimeFormat(
    "zh-CN",
    {
      month: "numeric",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    },
  ).format(date);
}

function scrollToBottom(smooth = true) {
  window.requestAnimationFrame(() => {
    elements.messageList.scrollTo({
      top: elements.messageList.scrollHeight,
      behavior: smooth ? "smooth" : "auto",
    });
  });
}

function toggleSidebar() {
  const isOpen = elements.sidebar.classList.toggle(
    "is-open",
  );

  elements.sidebarBackdrop.hidden = !isOpen;
}

function closeSidebar() {
  elements.sidebar.classList.remove("is-open");
  elements.sidebarBackdrop.hidden = true;
}

function sleep(milliseconds) {
  return new Promise((resolve) => {
    window.setTimeout(resolve, milliseconds);
  });
}