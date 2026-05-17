const state = {
  conversations: [],
  currentConversation: null,
  generating: false,
  hasSavedSettings: false,
  importedFeed: [],
  importedHistory: { offset: 0, limit: 80, total: 0 },
  importMode: "fetch",
  importFetchSettings: {},
  settings: {
    base_url: "https://api.openai.com/v1",
    api_key: "",
    model: "gpt-4.1-mini",
    system_prompt: "",
    user_message_suffix: "",
    prepend_chat_context: false,
    append_tail_user_prompt: true,
    context_k: 40,
    temperature: 0.7,
    thinking_enabled: true,
    reasoning_effort: "high",
  },
};

const ASSISTANT_MESSAGE_SEPARATOR = "&n&";
const SETTINGS_KEY = "ai-chat-settings";
const FETCH_SETTINGS_KEY = "ai-chat-fetch-settings";

const nodes = {
  list: document.querySelector("#conversationList"),
  title: document.querySelector("#conversationTitle"),
  status: document.querySelector("#statusText"),
  messages: document.querySelector("#messages"),
  form: document.querySelector("#chatForm"),
  input: document.querySelector("#messageInput"),
  send: document.querySelector("#sendBtn"),
  generate: document.querySelector("#generateBtn"),
  inlinePrependContext: document.querySelector("#inlinePrependContextInput"),
  inlineContextK: document.querySelector("#inlineContextKInput"),
  historyBtn: document.querySelector("#historyBtn"),
  newChat: document.querySelector("#newChatBtn"),
  settings: document.querySelector("#settingsDialog"),
  settingsBtn: document.querySelector("#settingsBtn"),
  importTwinBtn: document.querySelector("#importTwinBtn"),
  checkpointBtn: document.querySelector("#checkpointBtn"),
  saveSettings: document.querySelector("#saveSettingsBtn"),
  importDialog: document.querySelector("#importDialog"),
  historyDialog: document.querySelector("#historyDialog"),
  closeHistoryBtn: document.querySelector("#closeHistoryBtn"),
  historyPrevBtn: document.querySelector("#historyPrevBtn"),
  historyNextBtn: document.querySelector("#historyNextBtn"),
  historyMeta: document.querySelector("#historyMeta"),
  historyMessages: document.querySelector("#historyMessages"),
  importMode: document.querySelector("#importModeInput"),
  importPersona: document.querySelector("#importPersonaInput"),
  importTargetSender: document.querySelector("#importTargetSenderInput"),
  importJson: document.querySelector("#importJsonInput"),
  importJsonWrap: document.querySelector("#importJsonWrap"),
  importFetchWrap: document.querySelector("#importFetchWrap"),
  importFetchUrl: document.querySelector("#importFetchUrlInput"),
  importFetchUsername: document.querySelector("#importFetchUsernameInput"),
  importFetchStartDb: document.querySelector("#importFetchStartDbInput"),
  importFetchPages: document.querySelector("#importFetchPagesInput"),
  importFetchSize: document.querySelector("#importFetchSizeInput"),
  importFetchHeaders: document.querySelector("#importFetchHeadersInput"),
  importFetchCookies: document.querySelector("#importFetchCookiesInput"),
  importFetchSenderMap: document.querySelector("#importFetchSenderMapInput"),
  importLlmSummarize: document.querySelector("#importLlmSummarizeInput"),
  importLlmSummaryMaxChars: document.querySelector("#importLlmSummaryMaxCharsInput"),
  importPrependChatContext: document.querySelector("#importPrependChatContextInput"),
  submitImport: document.querySelector("#submitImportBtn"),
  baseUrl: document.querySelector("#baseUrlInput"),
  apiKey: document.querySelector("#apiKeyInput"),
  model: document.querySelector("#modelInput"),
  systemPrompt: document.querySelector("#systemPromptInput"),
  userMessageSuffix: document.querySelector("#userMessageSuffixInput"),
  prependChatContext: document.querySelector("#prependChatContextInput"),
  appendTailUserPrompt: document.querySelector("#appendTailUserPromptInput"),
  temperature: document.querySelector("#temperatureInput"),
  thinkingEnabled: document.querySelector("#thinkingEnabledInput"),
  reasoningEffort: document.querySelector("#reasoningEffortInput"),
};

function ensureUserMessageSuffixControl() {
  if (nodes.userMessageSuffix) return;
  const systemPromptInput = nodes.systemPrompt;
  if (!systemPromptInput) return;
  const systemPromptLabel = systemPromptInput.closest("label");
  if (!systemPromptLabel || !systemPromptLabel.parentElement) return;

  const label = document.createElement("label");
  const span = document.createElement("span");
  span.textContent = "每条用户消息末尾追加";

  const textarea = document.createElement("textarea");
  textarea.id = "userMessageSuffixInput";
  textarea.rows = 4;
  textarea.placeholder =
    "例如：如果你需要发送多条消息，使用&n&作为间隔符。你必须在末尾添加 **Emotion:Sad** **MsgColor:#FFC0CB**。";

  label.append(span, textarea);
  systemPromptLabel.insertAdjacentElement("afterend", label);
  nodes.userMessageSuffix = textarea;
}

function readSettings() {
  const saved = localStorage.getItem(SETTINGS_KEY);
  if (!saved) return;
  try {
    state.settings = { ...state.settings, ...JSON.parse(saved) };
    state.hasSavedSettings = true;
  } catch {
    localStorage.removeItem(SETTINGS_KEY);
  }
}

function writeSettings() {
  localStorage.setItem(SETTINGS_KEY, JSON.stringify(state.settings));
}

function readFetchSettings() {
  const saved = localStorage.getItem(FETCH_SETTINGS_KEY);
  if (!saved) return;
  try {
    state.importFetchSettings = { ...JSON.parse(saved) };
  } catch {
    localStorage.removeItem(FETCH_SETTINGS_KEY);
  }
}

function writeFetchSettings() {
  localStorage.setItem(FETCH_SETTINGS_KEY, JSON.stringify(state.importFetchSettings));
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || `请求失败：${response.status}`);
  }
  return data;
}

function setStatus(text, isError = false) {
  nodes.status.textContent = text;
  nodes.status.classList.toggle("error-text", isError);
}

function scrollMessagesToBottom() {
  nodes.messages.scrollTop = nodes.messages.scrollHeight;
}

function createReasoningBlock(text) {
  if (!text) return null;
  const details = document.createElement("details");
  details.className = "reasoning-block";

  const summary = document.createElement("summary");
  summary.textContent = "思考过程";

  const reasoning = document.createElement("div");
  reasoning.className = "reasoning-content";
  reasoning.textContent = text;

  details.append(summary, reasoning);
  return details;
}

function parseEmotionMeta(rawText) {
  const text = String(rawText || "");
  const emotionMatch = text.match(/\*\*Emotion:([^*]+)\*\*/);
  const colorMatch = text.match(/\*\*MsgColor:([^*]+)\*\*/);
  const cleanText = stripEmotionTailPreview(text)
    .replace(/\*\*Emotion:([^*]+)\*\*/g, "")
    .replace(/\*\*MsgColor:([^*]+)\*\*/g, "")
    .trim();

  const emotion = emotionMatch ? emotionMatch[1].trim() : "";
  const rawColor = colorMatch ? colorMatch[1].trim() : "";
  const msgColor = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(rawColor) ? rawColor : "";
  return { cleanText, emotion, msgColor };
}

function stripUserSuffixForDisplay(text, sender) {
  if (sender !== "user") return String(text || "");
  const suffix = String(state.settings.user_message_suffix || "");
  if (!suffix.trim()) return String(text || "");
  return String(text || "").replace(suffix, "").replace(/\n+$/, "");
}

function stripEmotionTailPreview(text) {
  const emotionIndex = text.indexOf("**Emotion:");
  const colorIndex = text.indexOf("**MsgColor:");
  const indexes = [emotionIndex, colorIndex].filter((value) => value >= 0);
  if (!indexes.length) return text.trim();
  return text.slice(0, Math.min(...indexes)).trimEnd();
}

function getReadableTextColor(hexColor) {
  const value = String(hexColor || "").replace("#", "");
  const normalized = value.length === 3 ? value.split("").map((char) => char + char).join("") : value;
  if (!/^[0-9a-fA-F]{6}$/.test(normalized)) return "";
  const red = parseInt(normalized.slice(0, 2), 16);
  const green = parseInt(normalized.slice(2, 4), 16);
  const blue = parseInt(normalized.slice(4, 6), 16);
  const luminance = (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255;
  return luminance > 0.68 ? "var(--text)" : "#ffffff";
}

function applyEmotionUI(bubble, contentNode, emotionNode, rawText, sender) {
  const parsed = parseEmotionMeta(rawText);
  const text = sender === "assistant" ? parsed.cleanText : String(rawText || "");

  contentNode.textContent = text;
  if (sender === "assistant") {
    if (parsed.msgColor) {
      bubble.style.backgroundColor = parsed.msgColor;
      bubble.style.borderColor = parsed.msgColor;
      bubble.style.color = getReadableTextColor(parsed.msgColor);
      emotionNode.style.color = bubble.style.color;
    } else {
      bubble.style.backgroundColor = "";
      bubble.style.borderColor = "";
      bubble.style.color = "";
      emotionNode.style.color = "";
    }
    if (parsed.emotion) {
      emotionNode.textContent = parsed.emotion;
      emotionNode.hidden = false;
    } else {
      emotionNode.textContent = "";
      emotionNode.hidden = true;
    }
  } else {
    bubble.style.backgroundColor = "";
    bubble.style.borderColor = "";
    bubble.style.color = "";
    emotionNode.textContent = "";
    emotionNode.hidden = true;
    emotionNode.style.color = "";
  }
  return parsed;
}

function createMessageRow(message) {
  const row = document.createElement("div");
  row.className = `message-row ${message.sender === "user" ? "user" : "assistant"}`;
  if (message.error) row.classList.add("error");

  const bubble = document.createElement("div");
  bubble.className = "message-bubble";

  const meta = document.createElement("div");
  meta.className = "message-meta";
  const who = message.context ? `上下文${message.sender === "user" ? "用户" : "AI"}` : (message.sender === "user" ? "你" : "AI");
  meta.textContent = `${who} · ${message.time || ""}`;

  const content = document.createElement("div");
  content.className = "message-content";
  const emotion = document.createElement("div");
  emotion.className = "message-emotion";
  emotion.hidden = true;

  const displayContent = stripUserSuffixForDisplay(message.content || "", message.sender);

  if (message.sender === "assistant" && !message.error) {
    applyEmotionUI(bubble, content, emotion, displayContent, message.sender);
  } else {
    content.textContent = displayContent;
  }

  const reasoning = createReasoningBlock(message.reasoning_content || "");
  if (reasoning) bubble.append(reasoning);
  bubble.append(meta, content, emotion);
  row.append(bubble);
  return row;
}

function renderMessages() {
  const conversation = state.currentConversation;
  nodes.title.textContent = conversation?.title || "新对话";
  nodes.messages.innerHTML = "";

  const messages = [...state.importedFeed, ...(conversation?.messages || [])];
  if (!messages.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "开始一个新对话";
    nodes.messages.append(empty);
    return;
  }

  for (const message of messages) {
    nodes.messages.append(createMessageRow(message));
  }
  scrollMessagesToBottom();
}

function renderMessageList(container, messages) {
  container.innerHTML = "";
  if (!messages.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "No imported history";
    container.append(empty);
    return;
  }
  for (const message of messages) {
    container.append(createMessageRow(message));
  }
}

function renderConversationList() {
  nodes.list.innerHTML = "";
  if (!state.conversations.length) {
    const empty = document.createElement("div");
    empty.className = "conversation-preview sidebar-empty";
    empty.textContent = "还没有聊天记录";
    nodes.list.append(empty);
    return;
  }

  for (const item of state.conversations) {
    const button = document.createElement("button");
    button.className = "conversation-item";
    if (state.currentConversation?.id === item.id) button.classList.add("active");

    const main = document.createElement("span");
    main.className = "conversation-main";

    const title = document.createElement("span");
    title.className = "conversation-title";
    title.textContent = item.title || "新对话";

    const preview = document.createElement("span");
    preview.className = "conversation-preview";
    const previewSource = stripUserSuffixForDisplay(item.last_message || "", "user");
    preview.textContent = (previewSource ? parseEmotionMeta(previewSource).cleanText : "") || `${item.message_count || 0} 条消息`;

    const del = document.createElement("span");
    del.className = "delete-chat";
    del.textContent = "×";
    del.title = "删除";

    main.append(title, preview);
    button.append(main, del);
    button.addEventListener("click", () => loadConversation(item.id));
    del.addEventListener("click", async (event) => {
      event.stopPropagation();
      await deleteConversation(item.id);
    });
    nodes.list.append(button);
  }
}

function parseJsonObjectInput(text, fieldName) {
  const raw = String(text || "").trim();
  if (!raw) return {};
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new Error(`${fieldName} 必须是合法 JSON`);
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(`${fieldName} 必须是 JSON 对象`);
  }
  return parsed;
}

function updateImportModeUI() {
  const mode = nodes.importMode?.value || "fetch";
  state.importMode = mode;
  if (nodes.importFetchWrap) nodes.importFetchWrap.style.display = mode === "fetch" ? "" : "none";
  if (nodes.importJsonWrap) nodes.importJsonWrap.style.display = mode === "json" ? "" : "none";
}

function syncImportFetchForm() {
  const saved = state.importFetchSettings || {};
  nodes.importMode.value = saved.mode || "fetch";
  nodes.importFetchUrl.value = saved.url || "";
  nodes.importFetchUsername.value = saved.username || "";
  nodes.importFetchStartDb.value = saved.start_db || "message_0.db";
  nodes.importFetchPages.value = String(saved.pages || 10);
  nodes.importFetchSize.value = String(saved.size || 100);
  nodes.importFetchHeaders.value = saved.headers_text || "";
  nodes.importFetchCookies.value = saved.cookies_text || "";
  nodes.importFetchSenderMap.value = saved.sender_map_text || "";
  updateImportModeUI();
}

function collectImportFetchForm() {
  state.importFetchSettings = {
    mode: nodes.importMode.value || "fetch",
    url: nodes.importFetchUrl.value.trim(),
    username: nodes.importFetchUsername.value.trim(),
    start_db: nodes.importFetchStartDb.value.trim() || "message_0.db",
    pages: Number(nodes.importFetchPages.value || 10),
    size: Number(nodes.importFetchSize.value || 100),
    headers_text: nodes.importFetchHeaders.value || "",
    cookies_text: nodes.importFetchCookies.value || "",
    sender_map_text: nodes.importFetchSenderMap.value || "",
  };
  writeFetchSettings();
  return state.importFetchSettings;
}

function syncSettingsForm() {
  nodes.baseUrl.value = state.settings.base_url;
  nodes.apiKey.value = state.settings.api_key;
  nodes.model.value = state.settings.model;
  nodes.systemPrompt.value = state.settings.system_prompt;
  if (nodes.userMessageSuffix) nodes.userMessageSuffix.value = state.settings.user_message_suffix || "";
  nodes.prependChatContext.checked = !!state.settings.prepend_chat_context;
  if (nodes.appendTailUserPrompt) {
    nodes.appendTailUserPrompt.checked = state.settings.append_tail_user_prompt !== false;
  }
  if (nodes.inlinePrependContext) nodes.inlinePrependContext.checked = !!state.settings.prepend_chat_context;
  if (nodes.inlineContextK) nodes.inlineContextK.value = String(state.settings.context_k || 40);
  nodes.temperature.value = state.settings.temperature;
  nodes.thinkingEnabled.checked = state.settings.thinking_enabled !== false;
  nodes.reasoningEffort.value = state.settings.reasoning_effort === "max" ? "max" : "high";
}

function collectSettingsForm() {
  state.settings = {
    base_url: nodes.baseUrl.value.trim() || "https://api.openai.com/v1",
    api_key: nodes.apiKey.value.trim(),
    model: nodes.model.value.trim() || "gpt-4.1-mini",
    system_prompt: nodes.systemPrompt.value,
    user_message_suffix: nodes.userMessageSuffix ? nodes.userMessageSuffix.value : "",
    prepend_chat_context: nodes.prependChatContext.checked,
    append_tail_user_prompt: nodes.appendTailUserPrompt ? nodes.appendTailUserPrompt.checked : true,
    context_k: Number(nodes.inlineContextK?.value || state.settings.context_k || 40),
    temperature: Number(nodes.temperature.value || 0.7),
    thinking_enabled: nodes.thinkingEnabled.checked,
    reasoning_effort: nodes.reasoningEffort.value === "max" ? "max" : "high",
  };
  writeSettings();
}

async function loadConfig() {
  const config = await requestJson("/api/config");
  if (!state.hasSavedSettings) {
    state.settings.base_url = config.base_url || state.settings.base_url;
    state.settings.model = config.model || state.settings.model;
  }
  if (!state.settings.api_key && config.has_env_api_key) {
    setStatus("已使用环境变量 API Key");
  }
}

async function loadImportedFeed() {
  const data = await requestJson("/api/imported-history?offset=0&limit=5000");
  state.importedFeed = Array.isArray(data.messages) ? data.messages : [];
}

async function loadImportedHistory(offset = state.importedHistory.offset) {
  const limit = state.importedHistory.limit || 80;
  const data = await requestJson(`/api/imported-history?offset=${encodeURIComponent(offset)}&limit=${encodeURIComponent(limit)}`);
  const messages = Array.isArray(data.messages) ? data.messages : [];
  state.importedHistory.offset = Number(data.offset || 0);
  state.importedHistory.limit = Number(data.limit || limit);
  state.importedHistory.total = Number(data.total || 0);
  renderMessageList(nodes.historyMessages, messages);

  const start = messages.length ? state.importedHistory.offset + 1 : 0;
  const end = state.importedHistory.offset + messages.length;
  nodes.historyMeta.textContent = `${start}-${end} / ${state.importedHistory.total}`;
  nodes.historyPrevBtn.disabled = state.importedHistory.offset <= 0;
  nodes.historyNextBtn.disabled = end >= state.importedHistory.total;
}

async function loadConversations() {
  const data = await requestJson("/api/conversations");
  state.conversations = data.conversations || [];
  renderConversationList();
}

async function loadConversation(id) {
  if (state.generating) return;
  state.currentConversation = await requestJson(`/api/conversations/${encodeURIComponent(id)}`);
  renderConversationList();
  renderMessages();
  setStatus("本地 JSON 存储");
}

async function createConversation() {
  if (state.generating) return;
  state.currentConversation = await requestJson("/api/conversations", {
    method: "POST",
    body: JSON.stringify({ title: "新对话" }),
  });
  await loadConversations();
  renderMessages();
  nodes.input.focus();
}

async function deleteConversation(id) {
  if (state.generating) return;
  await requestJson(`/api/conversations/${encodeURIComponent(id)}`, { method: "DELETE" });
  if (state.currentConversation?.id === id) {
    state.currentConversation = null;
    renderMessages();
  }
  await loadConversations();
}

async function createCheckpoint() {
  if (!state.currentConversation?.id) {
    setStatus("请先进入一个会话", true);
    return;
  }
  const label = window.prompt("输入 checkpoint 名称（可留空）", "") || "";
  const data = await requestJson("/api/checkpoints", {
    method: "POST",
    body: JSON.stringify({
      conversation_id: state.currentConversation.id,
      label,
    }),
  });
  setStatus(`已创建存档点：${data.checkpoint?.label || data.checkpoint?.id || ""}`);
}

async function restoreCheckpoint() {
  if (!state.currentConversation?.id) {
    setStatus("请先进入一个会话", true);
    return;
  }
  const data = await requestJson(`/api/checkpoints?conversation_id=${encodeURIComponent(state.currentConversation.id)}`);
  const checkpoints = data.checkpoints || [];
  if (!checkpoints.length) {
    setStatus("当前会话没有存档点", true);
    return;
  }
  const top = checkpoints.slice(0, 8);
  const options = top.map((item, index) => `${index + 1}. ${item.label} (${item.created_at})`).join("\n");
  const raw = window.prompt(`选择要恢复的存档点编号:\n${options}`, "1");
  const idx = Number(raw || 0) - 1;
  if (!Number.isInteger(idx) || idx < 0 || idx >= top.length) return;
  const target = top[idx];
  const restored = await requestJson("/api/checkpoints/restore", {
    method: "POST",
    body: JSON.stringify({ checkpoint_id: target.id }),
  });
  state.currentConversation = restored.conversation;
  await loadConversations();
  renderMessages();
  setStatus(`已恢复存档点：${target.label}`);
}

function parseSseEvents(buffer) {
  const parts = buffer.split("\n\n");
  const rest = parts.pop() || "";
  const events = parts.map((block) => {
    let eventName = "message";
    const dataLines = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) eventName = line.slice(6).trim();
      if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
    }
    return { event: eventName, data: dataLines.join("\n") };
  });
  return { events, rest };
}

function appendMessage(message) {
  const empty = nodes.messages.querySelector(".empty-state");
  if (empty) empty.remove();
  const row = createMessageRow(message);
  nodes.messages.append(row);
  scrollMessagesToBottom();
  return row.querySelector(".message-content");
}

function appendAssistantDraft() {
  const empty = nodes.messages.querySelector(".empty-state");
  if (empty) empty.remove();
  const row = createMessageRow({ sender: "assistant", content: "", time: "生成中" });
  row.dataset.draft = "assistant";
  const content = row.querySelector(".message-content");
  const bubble = row.querySelector(".message-bubble");
  const emotion = row.querySelector(".message-emotion");

  const reasoningBlock = document.createElement("details");
  reasoningBlock.className = "reasoning-block";
  reasoningBlock.hidden = true;
  reasoningBlock.open = true;

  const summary = document.createElement("summary");
  summary.textContent = "思考过程";
  const reasoning = document.createElement("div");
  reasoning.className = "reasoning-content";
  reasoningBlock.append(summary, reasoning);
  bubble.insertBefore(reasoningBlock, bubble.firstChild);
  nodes.messages.append(row);
  scrollMessagesToBottom();
  return { content, reasoning, reasoningBlock, bubble, emotion };
}

async function readChatStream(response, assistantDraft) {
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.error || `请求失败：${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let assistantText = "";
  let reasoningText = "";
  let completed = false;
  let activeDraft = assistantDraft;

  while (!completed) {
    const { value, done } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const parsed = parseSseEvents(buffer);
    buffer = parsed.rest;

    for (const item of parsed.events) {
      const payload = item.data ? JSON.parse(item.data) : {};
      if (item.event === "conversation") {
        state.currentConversation = payload.conversation;
        nodes.title.textContent = state.currentConversation?.title || "新对话";
      }
      if (item.event === "delta") {
        assistantText += payload.content || "";
        applyEmotionUI(activeDraft.bubble, activeDraft.content, activeDraft.emotion, assistantText, "assistant");
      }
      if (item.event === "reasoning_delta") {
        reasoningText += payload.content || "";
        assistantDraft.reasoning.textContent = reasoningText;
        assistantDraft.reasoningBlock.hidden = !reasoningText;
      }
      if (item.event === "agent_trace") {
        const ms = payload?.memory_steward || {};
        setStatus(`记忆命中 ${Number(ms.memory_hits || 0)} 条，上下文 ${Number(ms.context_count || 0)} 条`);
      }
      if (item.event === "done") {
        state.currentConversation = payload.conversation;
        nodes.messages.querySelectorAll('[data-draft="assistant"]').forEach((row) => {
          const content = row.querySelector(".message-content")?.textContent || "";
          if (!content.trim()) row.remove();
        });
        completed = true;
        await reader.cancel().catch(() => {});
        break;
      }
      if (item.event === "error") {
        state.currentConversation = payload.conversation || state.currentConversation;
        throw new Error(payload.error || "模型请求失败");
      }
    }
  }

  buffer += decoder.decode();
  if (buffer.trim()) {
    const parsed = parseSseEvents(`${buffer}\n\n`);
    for (const item of parsed.events) {
      const payload = item.data ? JSON.parse(item.data) : {};
      if (item.event === "done") state.currentConversation = payload.conversation;
      if (item.event === "error") throw new Error(payload.error || "模型请求失败");
    }
  }

  return assistantText;
}

async function sendMessage(text) {
  const suffix = String(state.settings.user_message_suffix || "").trim();
  const outgoingText = suffix ? `${text}${text.endsWith("\n") ? "" : "\n"}${suffix}` : text;
  const optimistic = { sender: "user", content: text, time: "刚刚" };
  if (!state.currentConversation) {
    state.currentConversation = { title: text.slice(0, 24) || "新对话", messages: [] };
  }
  state.currentConversation.messages.push(optimistic);
  nodes.messages.append(createMessageRow(optimistic));
  scrollMessagesToBottom();

  try {
    const data = await requestJson("/api/messages", {
      method: "POST",
      body: JSON.stringify({
        conversation_id: state.currentConversation?.id || "",
        message: outgoingText,
      }),
    });
    state.currentConversation = data.conversation;
    await loadConversations();
    renderMessages();
    setStatus("用户消息已保存");
  } catch (error) {
    setStatus(error.message, true);
    if (state.currentConversation?.messages?.length) {
      state.currentConversation.messages.pop();
    }
    if (!state.currentConversation?.id && !state.currentConversation?.messages?.length) {
      state.currentConversation = null;
    }
    renderMessages();
  }
}

async function generateReply() {
  if (state.generating) return;
  if (!state.currentConversation?.id) {
    setStatus("请先发送至少一条用户消息", true);
    return;
  }
  const messages = state.currentConversation.messages || [];
  if (!messages.length || messages[messages.length - 1]?.sender !== "user") {
    setStatus("当前没有新的用户消息可生成", true);
    return;
  }

  state.generating = true;
  nodes.generate.disabled = true;
  nodes.generate.textContent = "生成中";
  setStatus("正在请求模型...");
  const assistantDraft = appendAssistantDraft();

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        conversation_id: state.currentConversation.id,
        settings: state.settings,
      }),
    });
    await readChatStream(response, assistantDraft);
    await loadConversations();
    renderMessages();
    setStatus("已保存到 JSON");
  } catch (error) {
    setStatus(error.message, true);
    appendMessage({ sender: "assistant", content: `请求失败：${error.message}`, time: "刚刚", error: true });
    renderMessages();
  } finally {
    state.generating = false;
    nodes.generate.disabled = false;
    nodes.generate.textContent = "生成";
    nodes.input.focus();
  }
}

async function importTwinProfile() {
  const commonPayload = {
    persona_name: nodes.importPersona.value.trim(),
    target_sender: nodes.importTargetSender.value.trim() || "assistant",
    llm_summarize: !!nodes.importLlmSummarize.checked,
    llm_summary_max_chars: Number(nodes.importLlmSummaryMaxChars?.value || 220),
    prepend_chat_context: !!nodes.importPrependChatContext.checked,
    settings: state.settings,
  };

  let data;
  if ((nodes.importMode?.value || "fetch") === "json") {
    setStatus("正在导入粘贴的画像数据...");
    data = await requestJson("/api/twin/import", {
      method: "POST",
      body: JSON.stringify({
        ...commonPayload,
        raw_json: nodes.importJson.value.trim(),
      }),
    });
  } else {
    const fetchSettings = collectImportFetchForm();
    const headers = parseJsonObjectInput(fetchSettings.headers_text, "Headers");
    const cookies = parseJsonObjectInput(fetchSettings.cookies_text, "Cookies");
    const senderMap = parseJsonObjectInput(fetchSettings.sender_map_text, "sender_map");
    setStatus("正在抓取并导入画像数据...");
    data = await requestJson("/api/twin/fetch-import", {
      method: "POST",
      body: JSON.stringify({
        ...commonPayload,
        fetch_config: {
          url: fetchSettings.url,
          username: fetchSettings.username,
          start_db: fetchSettings.start_db,
          pages: Number(fetchSettings.pages || 10),
          size: Number(fetchSettings.size || 100),
          headers,
          cookies,
          sender_map: senderMap,
        },
      }),
    });
  }

  const summary = data?.profile?.summary || "已完成";
  const llmSummary = data?.llm_summary ? `；LLM总结：${data.llm_summary}` : "";
  const fetchedCount = Number(data?.fetch?.fetched_count || 0);
  const fetchedTip = fetchedCount > 0 ? `；抓取 ${fetchedCount} 条` : "";
  setStatus(`画像导入完成：${summary}${fetchedTip}${llmSummary}`);
}

function resizeInput() {
  nodes.input.style.height = "auto";
  nodes.input.style.height = `${Math.min(nodes.input.scrollHeight, 180)}px`;
}

nodes.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = nodes.input.value.trim();
  if (!text) return;
  nodes.input.value = "";
  resizeInput();
  await sendMessage(text);
});

nodes.input.addEventListener("input", resizeInput);
nodes.input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    nodes.form.requestSubmit();
  }
});

nodes.newChat.addEventListener("click", createConversation);
nodes.generate.addEventListener("click", generateReply);
nodes.historyBtn.addEventListener("click", async () => {
  try {
    state.importedHistory.offset = 0;
    await loadImportedHistory(0);
    nodes.historyDialog.showModal();
  } catch (error) {
    setStatus(error.message, true);
  }
});
nodes.closeHistoryBtn.addEventListener("click", () => {
  nodes.historyDialog.close();
});
nodes.historyPrevBtn.addEventListener("click", async () => {
  const nextOffset = Math.max(0, state.importedHistory.offset - state.importedHistory.limit);
  try {
    await loadImportedHistory(nextOffset);
  } catch (error) {
    setStatus(error.message, true);
  }
});
nodes.historyNextBtn.addEventListener("click", async () => {
  const nextOffset = state.importedHistory.offset + state.importedHistory.limit;
  try {
    await loadImportedHistory(nextOffset);
  } catch (error) {
    setStatus(error.message, true);
  }
});

if (nodes.inlinePrependContext) {
  nodes.inlinePrependContext.addEventListener("change", () => {
    state.settings.prepend_chat_context = !!nodes.inlinePrependContext.checked;
    if (nodes.prependChatContext) nodes.prependChatContext.checked = !!nodes.inlinePrependContext.checked;
    writeSettings();
  });
}

if (nodes.inlineContextK) {
  nodes.inlineContextK.addEventListener("change", () => {
    const k = Number(nodes.inlineContextK.value || 40);
    state.settings.context_k = Number.isFinite(k) ? Math.max(1, Math.min(400, Math.floor(k))) : 40;
    nodes.inlineContextK.value = String(state.settings.context_k);
    writeSettings();
  });
}

nodes.importTwinBtn.addEventListener("click", () => {
  syncImportFetchForm();
  nodes.importDialog.showModal();
});

nodes.importMode.addEventListener("change", () => {
  updateImportModeUI();
  collectImportFetchForm();
});

nodes.submitImport.addEventListener("click", async () => {
  try {
    await importTwinProfile();
    await loadImportedFeed();
    renderMessages();
    nodes.importDialog.close();
  } catch (error) {
    setStatus(error.message, true);
  }
});

nodes.checkpointBtn.addEventListener("click", async () => {
  try {
    const restoreMode = window.confirm("确定=恢复存档点，取消=创建新存档点");
    if (restoreMode) {
      await restoreCheckpoint();
    } else {
      await createCheckpoint();
    }
  } catch (error) {
    setStatus(error.message, true);
  }
});

nodes.settingsBtn.addEventListener("click", () => {
  syncSettingsForm();
  nodes.settings.showModal();
});

nodes.saveSettings.addEventListener("click", () => {
  collectSettingsForm();
  if (nodes.inlinePrependContext) nodes.inlinePrependContext.checked = !!state.settings.prepend_chat_context;
  if (nodes.inlineContextK) nodes.inlineContextK.value = String(state.settings.context_k || 40);
  nodes.settings.close();
  setStatus("设置已保存到浏览器");
});

async function boot() {
  ensureUserMessageSuffixControl();
  readSettings();
  readFetchSettings();
  try {
    await loadConfig();
    syncImportFetchForm();
    syncSettingsForm();
    await loadImportedFeed();
    await loadConversations();
    if (state.conversations[0]) {
      await loadConversation(state.conversations[0].id);
    } else {
      renderMessages();
    }
  } catch (error) {
    setStatus(error.message, true);
    renderMessages();
  }
}

boot();
