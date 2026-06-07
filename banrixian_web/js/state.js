import { DEFAULT_API_BASE } from "./config.js";

const TOKEN_KEY = "banrixian.token";
const USER_KEY = "banrixian.user";
const API_BASE_KEY = "banrixian.apiBase";
const CHAT_KEY = "banrixian.chatState";

let _syncTimer = null;
let _syncPending = false;

export const state = {
  token: localStorage.getItem(TOKEN_KEY) || "",
  user: readJson(USER_KEY),
  apiBase: resolveApiBase(),
  activeRoute: "chat",
  chatMessages: readJson(CHAT_KEY)?.messages || [],
  lastPlan: readJson(CHAT_KEY)?.lastPlan || null,
};

function readJson(key) {
  try {
    return JSON.parse(localStorage.getItem(key) || "null");
  } catch {
    return null;
  }
}

function resolveApiBase() {
  const stored = localStorage.getItem(API_BASE_KEY) || "";
  if (!stored || stored.includes("127.0.0.1:3000") || stored.includes("120.46.81.119:3000")) {
    localStorage.setItem(API_BASE_KEY, DEFAULT_API_BASE);
    return DEFAULT_API_BASE;
  }
  return stored;
}

export function setSession(token, user) {
  state.token = token || "";
  state.user = user || null;
  if (state.token) localStorage.setItem(TOKEN_KEY, state.token);
  else localStorage.removeItem(TOKEN_KEY);
  if (state.user) localStorage.setItem(USER_KEY, JSON.stringify(state.user));
  else localStorage.removeItem(USER_KEY);
}

export function setApiBase(value) {
  state.apiBase = (value || DEFAULT_API_BASE).replace(/\/+$/, "");
  localStorage.setItem(API_BASE_KEY, state.apiBase);
}

export function addChatMessage(message) {
  state.chatMessages = [...state.chatMessages, message].slice(-80);
  saveChatState();
}

export function setLastPlan(plan) {
  state.lastPlan = plan || null;
  saveChatState();
}

export function clearChatState() {
  state.chatMessages = [];
  state.lastPlan = null;
  localStorage.removeItem(CHAT_KEY);
}

function saveChatState() {
  localStorage.setItem(CHAT_KEY, JSON.stringify({
    messages: state.chatMessages,
    lastPlan: state.lastPlan,
  }));
  // 防抖 30s 后同步到服务端
  if (_syncTimer) clearTimeout(_syncTimer);
  _syncTimer = setTimeout(() => _pushToServer(), 30000);
}

function _pushToServer() {
  if (!state.token) return;
  import("./api.js").then(({ memoryApi }) => {
    memoryApi.pushChatSync({
      messages: state.chatMessages.slice(-60),
      lastPlan: state.lastPlan,
    }).catch(() => {});
  });
}

/** 登录后从服务端拉取聊天记录，若本地为空才覆盖 */
export async function pullChatFromServer() {
  if (!state.token) return;
  try {
    const { memoryApi } = await import("./api.js");
    const res = await memoryApi.pullChatSync();
    const serverMsgs = res?.data?.messages;
    const serverPlan = res?.data?.lastPlan;
    // 只有本地没有记录时才用服务端数据（保留本地最新的）
    if (Array.isArray(serverMsgs) && serverMsgs.length > 0 && state.chatMessages.length === 0) {
      state.chatMessages = serverMsgs;
      if (serverPlan) state.lastPlan = serverPlan;
      saveChatState();
    }
  } catch { /* 网络失败静默 */ }
}

/** 立即强制推送（退出登录前调用）*/
export function flushChatSync() {
  if (_syncTimer) { clearTimeout(_syncTimer); _syncTimer = null; }
  _pushToServer();
}

export function clearSession() {
  flushChatSync();
  setSession("", null);
}
