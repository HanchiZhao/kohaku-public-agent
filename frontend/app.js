"use strict";

const API_BASE = "/api/v1";

const state = {
  sessions: [],
  activeSessionId: null,
  messagesBySession: new Map(),
  loadedHistorySessions: new Set(),
  draftsBySession: new Map(),
  generationJobs: new Map(),
  isCreatingSession: false,
  errorTimeout: null,
};

class ApiError extends Error {
  constructor(status, detail) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
  }
}

class OutputMarkerFilter {
  constructor() {
    this.pending = "";
  }

  push(text) {
    if (!text) return "";

    this.pending = stripOutputMarkers(
      this.pending + String(text),
    );

    const retained =
      getPotentialMarkerSuffixLength(this.pending);

    const safeLength =
      this.pending.length - retained;

    const safeText =
      this.pending.slice(0, safeLength);

    this.pending =
      this.pending.slice(safeLength);

    return safeText;
  }

  flush() {
    const text = stripOutputMarkers(this.pending);
    this.pending = "";
    return text;
  }

  reset() {
    this.pending = "";
  }
}

class ProgressiveTextRenderer {
  constructor(sessionId, message) {
    this.sessionId = sessionId;
    this.message = message;
    this.buffer = "";
    this.timerId = null;
    this.resolveDrain = null;
    this.drainPromise = Promise.resolve();
    this.markerFilter = new OutputMarkerFilter();
  }

  enqueue(rawText) {
    const text = this.markerFilter.push(rawText);

    if (!text) return;

    let normalized = text;

    if (
      !this.message.content
      && !this.buffer
    ) {
      normalized = normalized.replace(
        /^(?:\r?\n)+/,
        "",
      );
    }

    if (!normalized) return;

    this.buffer += normalized;

    if (this.timerId === null) {
      this.drainPromise = new Promise(
        (resolve) => {
          this.resolveDrain = resolve;
        },
      );

      this.schedule();
    }
  }

  schedule() {
    this.timerId = window.setTimeout(
      () => this.tick(),
      52,
    );
  }

  tick() {
    this.timerId = null;

    if (!this.buffer) {
      this.finishDrain();
      return;
    }

    const count = this.charactersPerTick();

    const nextText =
      this.buffer.slice(0, count);

    this.buffer =
      this.buffer.slice(nextText.length);

    this.message.content += nextText;
    this.message.pending = false;

    updateMessageIfVisible(
      this.sessionId,
      this.message,
    );

    if (this.buffer) {
      this.schedule();
    } else {
      this.finishDrain();
    }
  }

  charactersPerTick() {
    const remaining = this.buffer.length;

    if (remaining > 5000) return 6;
    if (remaining > 2500) return 5;
    if (remaining > 1000) return 4;
    if (remaining > 350) return 3;

    return 2;
  }

  finishDrain() {
    if (!this.resolveDrain) return;

    const resolve = this.resolveDrain;

    this.resolveDrain = null;
    resolve();
  }

  async finish() {
    const trailing =
      this.markerFilter.flush();

    if (trailing) {
      this.enqueue(trailing);
    }

    await this.drainPromise;
  }

  flush() {
    if (this.timerId !== null) {
      window.clearTimeout(this.timerId);
      this.timerId = null;
    }

    const trailing =
      this.markerFilter.flush();

    if (trailing) {
      this.buffer += trailing;
    }

    if (this.buffer) {
      this.message.content += this.buffer;
      this.message.pending = false;
      this.buffer = "";

      updateMessageIfVisible(
        this.sessionId,
        this.message,
      );
    }

    this.finishDrain();
  }

  cancel() {
    if (this.timerId !== null) {
      window.clearTimeout(this.timerId);
    }

    this.timerId = null;
    this.buffer = "";

    this.markerFilter.reset();
    this.finishDrain();
  }
}

const elements = {
  sidebar:
    document.getElementById("sidebar"),

  sidebarBackdrop:
    document.getElementById("sidebarBackdrop"),

  mobileMenuButton:
    document.getElementById("mobileMenuButton"),

  healthIndicator:
    document.getElementById("healthIndicator"),

  healthText:
    document.getElementById("healthText"),

  newSessionButton:
    document.getElementById("newSessionButton"),

  deleteSessionButton:
    document.getElementById("deleteSessionButton"),

  sessionList:
    document.getElementById("sessionList"),

  conversationTitle:
    document.getElementById("conversationTitle"),

  conversationStatus:
    document.getElementById("conversationStatus"),

  errorBanner:
    document.getElementById("errorBanner"),

  errorText:
    document.getElementById("errorText"),

  dismissErrorButton:
    document.getElementById(
      "dismissErrorButton",
    ),

  messageList:
    document.getElementById("messageList"),

  emptyState:
    document.getElementById("emptyState"),

  composerForm:
    document.getElementById("composerForm"),

  messageInput:
    document.getElementById("messageInput"),

  characterCount:
    document.getElementById("characterCount"),

  sendButton:
    document.getElementById("sendButton"),

  stopButton:
    document.getElementById("stopButton"),
};

document.addEventListener(
  "DOMContentLoaded",
  initialize,
);

async function initialize() {
  bindEvents();
  updateCharacterCount();
  updateControls();

  if (!(await checkHealth())) {
    showError(
      "无法连接 Agent 后端。"
      + "请确认 Uvicorn 服务器已经启动。",
    );

    return;
  }

  try {
    await refreshSessions();

    if (state.sessions.length === 0) {
      await createNewSession();
    } else {
      await activateSession(
        state.sessions[0].session_id,
      );
    }
  } catch (error) {
    handleError(error);
  }

  window.setInterval(() => {
    void checkHealth();
    void refreshSessionsSafely();
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
      saveActiveDraft();
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
    () => void createNewSession(),
  );

  elements.deleteSessionButton.addEventListener(
    "click",
    () => void deleteActiveSession(),
  );

  elements.stopButton.addEventListener(
    "click",
    () => void stopGeneration(
      state.activeSessionId,
    ),
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

  document
    .querySelectorAll(".suggestion-button")
    .forEach((button) => {
      button.addEventListener(
        "click",
        () => {
          if (
            isActiveSessionGenerating()
          ) {
            return;
          }

          elements.messageInput.value =
            button.dataset.prompt || "";

          elements.messageInput.dispatchEvent(
            new Event(
              "input",
              {
                bubbles: true,
              },
            ),
          );

          elements.messageInput.focus();
        },
      );
    });
}

async function checkHealth() {
  try {
    const payload =
      await requestJson("/health");

    const healthy =
      payload.status === "ok"
      && payload.agent_service_started === true;

    elements.healthIndicator.dataset.state =
      healthy ? "healthy" : "error";

    elements.healthText.textContent =
      healthy
        ? "服务运行正常"
        : "Agent 服务不可用";

    return healthy;
  } catch {
    elements.healthIndicator.dataset.state =
      "error";

    elements.healthText.textContent =
      "无法连接服务";

    return false;
  }
}

async function refreshSessions() {
  const payload = await requestJson(
    `${API_BASE}/sessions`,
  );

  state.sessions = [...payload.sessions]
    .map((session) => ({
      ...session,

      is_busy:
        Boolean(session.is_busy)
        || state.generationJobs.has(
          session.session_id,
        ),
    }))
    .sort(
      (left, right) =>
        new Date(
          right.created_at,
        ).getTime()
        -
        new Date(
          left.created_at,
        ).getTime(),
    );

  renderSessions();
  updateHeader();
  updateControls();
}

async function createNewSession() {
  if (state.isCreatingSession) {
    return null;
  }

  clearError();
  saveActiveDraft();

  state.isCreatingSession = true;

  updateControls();

  try {
    const payload = await requestJson(
      `${API_BASE}/sessions`,
      {
        method: "POST",

        body: {
          name:
            `新对话 ${
              state.sessions.length + 1
            }`,
        },
      },
    );

    await refreshSessions();

    state.messagesBySession.set(
      payload.session_id,
      [],
    );

    state.loadedHistorySessions.add(
      payload.session_id,
    );

    await activateSession(
      payload.session_id,
    );

    return payload.session_id;
  } catch (error) {
    handleError(error);
    return null;
  } finally {
    state.isCreatingSession = false;
    updateControls();
  }
}

async function activateSession(sessionId) {
  if (!sessionId) return;

  clearError();
  saveActiveDraft();

  state.activeSessionId = sessionId;

  restoreActiveDraft();

  renderSessions();
  renderMessages();
  updateHeader();
  updateControls();
  closeSidebar();

  if (
    state.loadedHistorySessions.has(
      sessionId,
    )
  ) {
    elements.messageInput.focus();
    return;
  }

  try {
    const payload = await requestJson(
      `${API_BASE}/sessions/`
      + `${sessionId}/history`,
    );

    if (
      !state.generationJobs.has(sessionId)
    ) {
      state.messagesBySession.set(
        sessionId,
        normalizeHistoryMessages(
          payload.history,
        ),
      );
    }

    state.loadedHistorySessions.add(
      sessionId,
    );

    if (
      state.activeSessionId === sessionId
    ) {
      renderMessages();
    }
  } catch (error) {
    if (
      error instanceof ApiError
      && error.status === 404
    ) {
      await recoverFromMissingSession(
        sessionId,
      );

      return;
    }

    handleError(error);
  }

  elements.messageInput.focus();
}

async function deleteActiveSession() {
  const sessionId =
    state.activeSessionId;

  if (!sessionId) return;

  if (
    state.generationJobs.has(sessionId)
  ) {
    showError(
      "请先停止当前对话的回答，"
      + "再删除该对话。",
    );

    return;
  }

  const sessionName =
    findSession(sessionId)?.name
    || "当前对话";

  const confirmed = window.confirm(
    `确定删除“${sessionName}”吗？`
    + "\n该操作无法撤销。",
  );

  if (!confirmed) return;

  try {
    await requestJson(
      `${API_BASE}/sessions/${sessionId}`,
      {
        method: "DELETE",
      },
    );

    state.messagesBySession.delete(
      sessionId,
    );

    state.loadedHistorySessions.delete(
      sessionId,
    );

    state.draftsBySession.delete(
      sessionId,
    );

    state.activeSessionId = null;

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
  }
}

async function handleMessageSubmit(event) {
  event.preventDefault();

  let sessionId =
    state.activeSessionId;

  if (!sessionId) {
    sessionId =
      await createNewSession();

    if (!sessionId) return;
  }

  if (
    state.generationJobs.has(sessionId)
  ) {
    showError(
      "当前对话正在生成回答。"
      + "你可以停止它，"
      + "或切换到其他对话继续聊天。",
    );

    return;
  }

  const content =
    elements.messageInput.value.trim();

  if (!content) {
    showError("请输入消息内容。");
    elements.messageInput.focus();
    return;
  }

  clearError();

  const messages =
    getSessionMessages(sessionId);

  const userMessage =
    createMessage(
      "user",
      content,
    );

  const assistantMessage =
    createMessage(
      "assistant",
      "",
      {
        pending: true,
        pendingLabel: "正在思考",
      },
    );

  messages.push(
    userMessage,
    assistantMessage,
  );

  state.draftsBySession.set(
    sessionId,
    "",
  );

  elements.messageInput.value = "";

  updateCharacterCount();
  resizeMessageInput();
  renderMessages();

  const job = {
    sessionId,

    controller:
      new AbortController(),

    renderer:
      new ProgressiveTextRenderer(
        sessionId,
        assistantMessage,
      ),

    assistantMessage,
    stopRequested: false,
    slowStartTimer: null,
  };

  state.generationJobs.set(
    sessionId,
    job,
  );

  markSessionBusy(
    sessionId,
    true,
  );

  updateControls();
  updateHeader();
  renderSessions();
  scrollToBottom();

  job.slowStartTimer =
    window.setTimeout(
      () => {
        if (
          assistantMessage.pending
          && !assistantMessage.content
        ) {
          assistantMessage.pendingLabel =
            "模型正在推理，"
            + "首段输出可能稍慢";

          updateMessageIfVisible(
            sessionId,
            assistantMessage,
          );
        }
      },
      4000,
    );

  void runGenerationJob(
    sessionId,
    content,
    job,
  );
}

async function runGenerationJob(
  sessionId,
  content,
  job,
) {
  const {
    controller,
    renderer,
    assistantMessage,
  } = job;

  try {
    const response = await fetch(
      `${API_BASE}/sessions/`
      + `${sessionId}/messages/stream`,
      {
        method: "POST",

        headers: {
          "Accept":
            "text/event-stream",

          "Content-Type":
            "application/json",
        },

        body: JSON.stringify({
          content,
        }),

        signal: controller.signal,
      },
    );

    if (!response.ok) {
      throw await responseToApiError(
        response,
      );
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
          renderer.enqueue(
            String(payload.text || ""),
          );
        } else if (
          eventName === "error"
        ) {
          throw new ApiError(
            500,
            payload.detail
            || "流式回答失败。",
          );
        }
      },
    );

    await renderer.finish();

    assistantMessage.content =
      cleanAssistantText(
        assistantMessage.content,
      );

    if (
      !assistantMessage.content
      && !job.stopRequested
    ) {
      assistantMessage.content =
        "没有收到可显示的回答。";
    }
  } catch (error) {
    if (
      error instanceof DOMException
      && error.name === "AbortError"
      && job.stopRequested
    ) {
      renderer.cancel();

      assistantMessage.stopped = true;

      assistantMessage.content =
        cleanAssistantText(
          assistantMessage.content,
        )
        || "已停止生成。";
    } else {
      renderer.flush();

      assistantMessage.failed = true;

      assistantMessage.content =
        cleanAssistantText(
          assistantMessage.content,
        )
        || "回答生成失败，请稍后重试。";

      handleError(error);
    }
  } finally {
    window.clearTimeout(
      job.slowStartTimer,
    );

    if (job.stopRequested) {
      renderer.cancel();
    } else {
      renderer.flush();
    }

    assistantMessage.content =
      cleanAssistantText(
        assistantMessage.content,
      );

    assistantMessage.pending = false;

    state.generationJobs.delete(
      sessionId,
    );

    markSessionBusy(
      sessionId,
      false,
    );

    updateMessageIfVisible(
      sessionId,
      assistantMessage,
    );

    updateControls();
    updateHeader();
    renderSessions();

    await waitForSessionIdle(
      sessionId,
    );

    await refreshSessionsSafely();

    if (
      state.activeSessionId === sessionId
    ) {
      elements.messageInput.focus();
    }
  }
}

async function stopGeneration(sessionId) {
  if (!sessionId) return;

  const job =
    state.generationJobs.get(sessionId);

  if (!job) return;

  job.stopRequested = true;
  job.renderer.cancel();

  if (
    state.activeSessionId === sessionId
  ) {
    elements.stopButton.disabled = true;

    elements.conversationStatus.textContent =
      "正在停止";
  }

  const interruptRequest =
    requestJson(
      `${API_BASE}/sessions/`
      + `${sessionId}/interrupt`,
      {
        method: "POST",
      },
    );

  job.controller.abort();

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
  const reader =
    readableStream.getReader();

  const decoder =
    new TextDecoder("utf-8");

  let buffer = "";

  try {
    while (true) {
      const {
        value,
        done,
      } = await reader.read();

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

      let boundary =
        buffer.indexOf("\n\n");

      while (boundary !== -1) {
        const block =
          buffer.slice(0, boundary);

        buffer =
          buffer.slice(boundary + 2);

        if (block.trim()) {
          const event =
            parseSseBlock(block);

          onEvent(
            event.name,
            event.data,
          );
        }

        boundary =
          buffer.indexOf("\n\n");
      }

      if (done) {
        buffer += decoder.decode();
        break;
      }
    }

    if (buffer.trim()) {
      const event =
        parseSseBlock(buffer);

      onEvent(
        event.name,
        event.data,
      );
    }
  } finally {
    reader.releaseLock();
  }
}

function parseSseBlock(block) {
  let eventName = "message";
  const dataLines = [];

  for (
    const line
    of block.split("\n")
  ) {
    if (
      !line
      || line.startsWith(":")
    ) {
      continue;
    }

    const separator =
      line.indexOf(":");

    const field =
      separator === -1
        ? line
        : line.slice(0, separator);

    let value =
      separator === -1
        ? ""
        : line.slice(separator + 1);

    if (value.startsWith(" ")) {
      value = value.slice(1);
    }

    if (field === "event") {
      eventName = value;
    } else if (field === "data") {
      dataLines.push(value);
    }
  }

  const rawData =
    dataLines.join("\n");

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

function stripOutputMarkers(text) {
  let cleaned = String(text ?? "")
    .replace(
      /[\u200B-\u200D\u2060\uFEFF]/g,
      "",
    );

  const patterns = [
    /\\?\[\s*\/?\s*output(?:[_\s-]*(?:final|text))?\s*\/?\s*\\?\]/gi,

    /<\s*\/?\s*output(?:[_\s-]*(?:final|text))?\s*\/?\s*>/gi,

    /\{\{\s*\/?\s*output(?:[_\s-]*(?:final|text))?\s*\/?\s*\}\}/gi,

    /\(\s*\/?\s*output(?:[_\s-]*(?:final|text))?\s*\/?\s*\)/gi,
  ];

  let previous;

  do {
    previous = cleaned;

    for (const pattern of patterns) {
      cleaned =
        cleaned.replace(pattern, "");
    }
  } while (cleaned !== previous);

  return cleaned;
}

function cleanAssistantText(text) {
  return stripOutputMarkers(text)
    .replace(/^(?:\r?\n)+/, "")
    .replace(/(?:\r?\n)+$/, "");
}

function getPotentialMarkerSuffixLength(text) {
  const lower =
    String(text).toLowerCase();

  const scanStart =
    Math.max(0, lower.length - 64);

  const positions = [
    lower.lastIndexOf("["),
    lower.lastIndexOf("\\["),
    lower.lastIndexOf("<"),
    lower.lastIndexOf("{{"),
    lower.lastIndexOf("("),
  ].filter(
    (position) =>
      position >= scanStart,
  );

  if (positions.length === 0) {
    return 0;
  }

  let opening =
    Math.max(...positions);

  if (
    lower[opening] === "["
    && lower[opening - 1] === "\\"
  ) {
    opening -= 1;
  }

  const suffix =
    lower.slice(opening);

  const closed =
    suffix.endsWith("]")
    || suffix.endsWith("\\]")
    || suffix.endsWith(">")
    || suffix.endsWith("}}")
    || suffix.endsWith(")");

  if (closed) {
    return 0;
  }

  const probe = suffix
    .replace(/^\\?\[/, "")
    .replace(/^</, "")
    .replace(/^\{\{/, "")
    .replace(/^\(/, "")
    .replace(/^\s*\/?\s*/, "")
    .replace(/[\s_-]/g, "")
    .replace(/\\/g, "");

  const names = [
    "output",
    "outputfinal",
    "outputtext",
  ];

  return names.some(
    (name) =>
      name.startsWith(probe)
      || probe.startsWith(name),
  )
    ? suffix.length
    : 0;
}

function normalizeHistoryMessages(history) {
  const rawMessages =
    Array.isArray(history?.messages)
      ? history.messages
      : [];

  const normalized = [];

  for (
    const rawMessage
    of rawMessages
  ) {
    const role =
      normalizeRole(rawMessage);

    if (!role) continue;

    let content =
      extractTextContent(rawMessage);

    if (role === "assistant") {
      content =
        cleanAssistantText(content);
    }

    if (content.trim()) {
      normalized.push(
        createMessage(role, content),
      );
    }
  }

  return normalized;
}

function normalizeRole(message) {
  const role = String(
    message?.role
    ?? message?.sender
    ?? message?.type
    ?? message?.author
    ?? "",
  ).toLowerCase();

  if (
    role.includes("user")
    || role.includes("human")
  ) {
    return "user";
  }

  if (
    role.includes("assistant")
    || role.includes("agent")
    || role.includes("model")
  ) {
    return "assistant";
  }

  return null;
}

function extractTextContent(
  value,
  depth = 0,
) {
  if (
    depth > 5
    || value == null
  ) {
    return "";
  }

  if (
    typeof value === "string"
  ) {
    return value;
  }

  if (Array.isArray(value)) {
    return value
      .map(
        (item) =>
          extractTextContent(
            item,
            depth + 1,
          ),
      )
      .filter(Boolean)
      .join("");
  }

  if (
    typeof value === "object"
  ) {
    const keys = [
      "content",
      "text",
      "message",
      "value",
      "output_text",
    ];

    for (const key of keys) {
      if (key in value) {
        const extracted =
          extractTextContent(
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

    pending:
      options.pending === true,

    pendingLabel:
      options.pendingLabel
      || "正在思考",

    stopped:
      options.stopped === true,

    failed:
      options.failed === true,
  };
}

function createLocalId() {
  if (
    window.crypto
    && typeof window.crypto.randomUUID
      === "function"
  ) {
    return window.crypto.randomUUID();
  }

  return (
    `${Date.now()}-`
    + Math.random()
      .toString(16)
      .slice(2)
  );
}

function getSessionMessages(sessionId) {
  if (
    !state.messagesBySession.has(
      sessionId,
    )
  ) {
    state.messagesBySession.set(
      sessionId,
      [],
    );
  }

  return state.messagesBySession.get(
    sessionId,
  );
}

function getActiveMessages() {
  return state.activeSessionId
    ? getSessionMessages(
      state.activeSessionId,
    )
    : [];
}

function isActiveSessionGenerating() {
  return Boolean(
    state.activeSessionId
    && state.generationJobs.has(
      state.activeSessionId,
    ),
  );
}

function saveActiveDraft() {
  if (!state.activeSessionId) return;

  state.draftsBySession.set(
    state.activeSessionId,
    elements.messageInput.value,
  );
}

function restoreActiveDraft() {
  elements.messageInput.value =
    state.activeSessionId
      ? state.draftsBySession.get(
        state.activeSessionId,
      ) || ""
      : "";

  updateCharacterCount();
  resizeMessageInput();
}

function markSessionBusy(
  sessionId,
  busy,
) {
  state.sessions =
    state.sessions.map(
      (session) =>
        session.session_id
          === sessionId
          ? {
            ...session,
            is_busy: busy,
          }
          : session,
    );
}

function renderMessages() {
  elements.messageList.replaceChildren();

  const messages =
    getActiveMessages();

  if (messages.length === 0) {
    elements.messageList.append(
      elements.emptyState,
    );

    return;
  }

  for (const message of messages) {
    elements.messageList.append(
      createMessageElement(message),
    );
  }

  scrollToBottom(false);
}

function updateMessageIfVisible(
  sessionId,
  message,
) {
  if (
    state.activeSessionId !== sessionId
  ) {
    return;
  }

  const existing =
    elements.messageList.querySelector(
      `[data-message-id="${message.id}"]`,
    );

  if (existing) {
    existing.replaceWith(
      createMessageElement(message),
    );
  }

  scrollToBottom();
}

function createMessageElement(message) {
  const article =
    document.createElement("article");

  article.className =
    `message message-${message.role}`;

  article.dataset.messageId =
    message.id;

  const avatar =
    document.createElement("div");

  avatar.className =
    "message-avatar";

  avatar.textContent =
    message.role === "user"
      ? "你"
      : "K";

  const body =
    document.createElement("div");

  body.className =
    "message-body";

  const role =
    document.createElement("div");

  role.className =
    "message-role";

  role.textContent =
    message.role === "user"
      ? "你"
      : "Kohaku Public Agent";

  const content =
    document.createElement("div");

  content.className =
    "message-content";

  if (
    message.pending
    && !message.content
  ) {
    const placeholder =
      document.createElement("span");

    placeholder.className =
      "typing-placeholder";

    placeholder.textContent =
      message.pendingLabel
      || "正在思考";

    content.append(placeholder);
  } else {
    renderSafeContent(
      content,

      message.role === "assistant"
        ? cleanAssistantText(
          message.content,
        )
        : message.content,
    );
  }

  body.append(role, content);

  if (
    message.stopped
    || message.failed
  ) {
    const status =
      document.createElement("div");

    status.className =
      "message-status";

    status.textContent =
      message.stopped
        ? "已停止生成"
        : "生成未完整完成";

    body.append(status);
  }

  article.append(
    avatar,
    body,
  );

  return article;
}

function renderSafeContent(
  container,
  text,
) {
  container.replaceChildren();

  const sections =
    String(text).split("```");

  sections.forEach(
    (section, index) => {
      if (index % 2 === 0) {
        if (!section) return;

        const block =
          document.createElement("div");

        block.className =
          "text-block";

        block.textContent =
          section;

        container.append(block);

        return;
      }

      let codeText = section;
      let language = "";

      const firstLineEnd =
        section.indexOf("\n");

      if (firstLineEnd !== -1) {
        const candidate =
          section
            .slice(0, firstLineEnd)
            .trim();

        if (
          /^[a-zA-Z0-9_+#.-]{1,20}$/
            .test(candidate)
        ) {
          language = candidate;

          codeText =
            section.slice(
              firstLineEnd + 1,
            );
        }
      }

      const pre =
        document.createElement("pre");

      pre.className =
        "code-block";

      if (language) {
        const label =
          document.createElement("div");

        label.className =
          "code-language";

        label.textContent =
          language;

        pre.append(label);
      }

      const code =
        document.createElement("code");

      code.textContent =
        codeText;

      pre.append(code);
      container.append(pre);
    },
  );
}

function renderSessions() {
  elements.sessionList.replaceChildren();

  if (state.sessions.length === 0) {
    const empty =
      document.createElement("div");

    empty.className =
      "session-empty";

    empty.textContent =
      "还没有对话记录";

    elements.sessionList.append(
      empty,
    );

    return;
  }

  for (
    const session
    of state.sessions
  ) {
    const button =
      document.createElement("button");

    button.type = "button";

    button.className =
      "session-button";

    button.setAttribute(
      "aria-current",

      String(
        session.session_id
        === state.activeSessionId,
      ),
    );

    const icon =
      document.createElement("span");

    icon.className =
      "session-icon";

    icon.textContent = "✦";

    const copy =
      document.createElement("span");

    copy.className =
      "session-copy";

    const name =
      document.createElement("span");

    name.className =
      "session-name";

    name.textContent =
      session.name;

    const meta =
      document.createElement("span");

    meta.className =
      "session-meta";

    if (
      session.is_busy
      || state.generationJobs.has(
        session.session_id,
      )
    ) {
      const dot =
        document.createElement("span");

      dot.className =
        "busy-dot";

      meta.append(dot);
    }

    const time =
      document.createElement("span");

    time.textContent =
      formatDate(
        session.created_at,
      );

    meta.append(time);

    copy.append(name, meta);
    button.append(icon, copy);

    button.addEventListener(
      "click",
      () => void activateSession(
        session.session_id,
      ),
    );

    elements.sessionList.append(
      button,
    );
  }
}

function updateHeader() {
  const session =
    findSession(
      state.activeSessionId,
    );

  elements.conversationTitle.textContent =
    session?.name
    || "未选择对话";

  if (
    isActiveSessionGenerating()
  ) {
    elements.conversationStatus.textContent =
      "正在生成";
  } else if (session?.is_busy) {
    elements.conversationStatus.textContent =
      "会话处理中";
  } else if (session) {
    elements.conversationStatus.textContent =
      "已连接";
  } else {
    elements.conversationStatus.textContent =
      "未连接会话";
  }
}

function updateControls() {
  const hasSession =
    Boolean(state.activeSessionId);

  const activeGenerating =
    isActiveSessionGenerating();

  const hasMessage =
    Boolean(
      elements.messageInput.value.trim(),
    );

  elements.newSessionButton.disabled =
    state.isCreatingSession;

  elements.deleteSessionButton.disabled =
    !hasSession
    || activeGenerating;

  elements.sendButton.disabled =
    !hasSession
    || !hasMessage
    || activeGenerating;

  elements.sendButton.hidden =
    activeGenerating;

  elements.stopButton.hidden =
    !activeGenerating;

  elements.stopButton.disabled =
    false;

  // 生成过程中输入框仍然保留，
  // 用户可以提前输入下一条消息。
  elements.messageInput.disabled =
    false;

  document
    .querySelectorAll(
      ".suggestion-button",
    )
    .forEach((button) => {
      button.disabled =
        activeGenerating;
    });

  renderSessions();
}

function updateCharacterCount() {
  elements.characterCount.textContent =
    `${elements.messageInput.value.length}`
    + " / 50000";
}

function resizeMessageInput() {
  elements.messageInput.style.height =
    "auto";

  elements.messageInput.style.height =
    `${Math.min(
      elements.messageInput.scrollHeight,
      180,
    )}px`;
}

async function waitForSessionIdle(
  sessionId,
  attempts = 20,
) {
  for (
    let attempt = 0;
    attempt < attempts;
    attempt += 1
  ) {
    try {
      const session =
        await requestJson(
          `${API_BASE}/sessions/`
          + sessionId,
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
      "Unable to refresh "
      + "session metadata:",
      error,
    );
  }
}

async function recoverFromMissingSession(
  missingId,
) {
  state.messagesBySession.delete(
    missingId,
  );

  state.loadedHistorySessions.delete(
    missingId,
  );

  state.draftsBySession.delete(
    missingId,
  );

  if (
    state.activeSessionId
    === missingId
  ) {
    state.activeSessionId = null;
  }

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

  if (
    options.body !== undefined
  ) {
    headers["Content-Type"] =
      "application/json";

    body =
      JSON.stringify(options.body);
  }

  const response = await fetch(
    path,
    {
      method:
        options.method || "GET",

      headers,
      body,

      signal:
        options.signal,
    },
  );

  if (!response.ok) {
    throw await responseToApiError(
      response,
    );
  }

  if (response.status === 204) {
    return null;
  }

  const text =
    await response.text();

  if (!text) return {};

  try {
    return JSON.parse(text);
  } catch {
    return {
      value: text,
    };
  }
}

async function responseToApiError(
  response,
) {
  let detail =
    `请求失败，状态码 `
    + response.status;

  try {
    const payload =
      await response.json();

    if (
      typeof payload.detail
      === "string"
    ) {
      detail = payload.detail;
    } else if (
      Array.isArray(payload.detail)
    ) {
      detail = payload.detail
        .map(
          (item) =>
            item.msg
            || "数据验证失败",
        )
        .join("；");
    }
  } catch {
    const text =
      await response.text();

    if (text) {
      detail = text;
    }
  }

  return new ApiError(
    response.status,
    detail,
  );
}

function handleError(error) {
  console.error(error);

  if (error instanceof ApiError) {
    if (error.status === 404) {
      showError(
        "该会话不存在或已经失效。",
      );
    } else if (
      error.status === 409
    ) {
      showError(
        "该会话正在生成回答。"
        + "请停止它，"
        + "或切换到其他对话。",
      );
    } else if (
      error.status === 422
    ) {
      showError(
        `输入内容无效：`
        + error.message,
      );
    } else if (
      error.status === 503
    ) {
      showError(
        "Agent 服务暂时不可用。",
      );
    } else {
      showError(error.message);
    }

    return;
  }

  showError(
    error?.message
    || "发生未知错误，"
    + "请检查服务器终端。",
  );
}

function showError(message) {
  clearTimeout(
    state.errorTimeout,
  );

  elements.errorText.textContent =
    message;

  elements.errorBanner.hidden =
    false;

  state.errorTimeout =
    window.setTimeout(
      clearError,
      10000,
    );
}

function clearError() {
  clearTimeout(
    state.errorTimeout,
  );

  state.errorTimeout = null;

  elements.errorText.textContent =
    "";

  elements.errorBanner.hidden =
    true;
}

function findSession(sessionId) {
  return state.sessions.find(
    (session) =>
      session.session_id
      === sessionId,
  );
}

function formatDate(value) {
  const date = new Date(value);

  if (
    Number.isNaN(
      date.getTime(),
    )
  ) {
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

function scrollToBottom(
  smooth = true,
) {
  window.requestAnimationFrame(
    () => {
      elements.messageList.scrollTo({
        top:
          elements.messageList.scrollHeight,

        behavior:
          smooth ? "smooth" : "auto",
      });
    },
  );
}

function toggleSidebar() {
  const open =
    elements.sidebar.classList.toggle(
      "is-open",
    );

  elements.sidebarBackdrop.hidden =
    !open;
}

function closeSidebar() {
  elements.sidebar.classList.remove(
    "is-open",
  );

  elements.sidebarBackdrop.hidden =
    true;
}

function sleep(milliseconds) {
  return new Promise(
    (resolve) =>
      window.setTimeout(
        resolve,
        milliseconds,
      ),
  );
}