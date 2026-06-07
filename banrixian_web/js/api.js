import { state } from "./state.js";

function endpoint(path) {
  return `${state.apiBase}${path.startsWith("/") ? path : `/${path}`}`;
}

function authHeaders(extra = {}) {
  const headers = { ...extra };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  return headers;
}

async function parseResponse(response) {
  let data = null;
  const text = await response.text();
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { message: text };
    }
  }
  if (!response.ok) {
    const detail = data?.detail;
    const message = errorMessage(detail)
      || errorMessage(data?.message)
      || `请求失败：${response.status}`;
    const error = new Error(message);
    error.data = data;
    error.status = response.status;
    throw error;
  }
  return data;
}

export function errorMessage(value) {
  if (!value) return "";
  if (typeof value === "string") {
    return value === "[object Object]" ? "请求失败，请稍后重试" : value;
  }
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (typeof value === "object") {
    return errorMessage(value.message)
      || errorMessage(value.msg)
      || errorMessage(value.detail)
      || errorMessage(value.error)
      || "请求失败，请稍后重试";
  }
  return String(value);
}

export async function apiGet(path) {
  const response = await fetch(endpoint(path), { headers: authHeaders() });
  return parseResponse(response);
}

export async function apiJson(path, method, body) {
  const response = await fetch(endpoint(path), {
    method,
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return parseResponse(response);
}

export const authApi = {
  login: (payload) => apiJson("/v1/auth/login", "POST", payload),
  register: (payload) => apiJson("/v1/auth/register", "POST", payload),
  captcha: () => apiJson("/v1/auth/captcha", "POST", {}),
};

export const userApi = {
  profile: () => apiGet("/v1/user/profile"),
  updateProfile: (payload) => apiJson("/v1/user/profile", "PUT", payload),
  sign: () => apiJson("/v1/user/sign", "POST"),
  family: () => apiGet("/v1/user/family"),
  addFamily: (payload) => apiJson("/v1/user/family", "POST", payload),
  updateFamily: (id, payload) => apiJson(`/v1/user/family/${id}`, "PUT", payload),
  deleteFamily: (id) => apiJson(`/v1/user/family/${id}`, "DELETE"),
  mergeFamilyTags: (id, tags) => apiJson(`/v1/user/family/${id}/tags`, "PATCH", { tags }),
};

export const contentApi = {
  services: () => apiGet("/v1/services/list"),
  nearby: () => apiGet("/v1/map/nearby?limit=3"),
  hotlist: () => apiGet("/v1/content/hotlist"),
  topRoutes: () => apiGet("/v1/map/top-routes?limit=5"),
  placeImage: (params = {}) => {
    const query = new URLSearchParams(params).toString();
    return apiGet(`/v1/map/place-image${query ? `?${query}` : ""}`);
  },
  weather: (params = {}) => {
    const query = new URLSearchParams(params).toString();
    return apiGet(`/v1/weather/current${query ? `?${query}` : ""}`);
  },
};

export const orderApi = {
  list: (status = "all", city = "all") => apiGet(`/v1/orders?status=${encodeURIComponent(status)}&city=${encodeURIComponent(city || "all")}`),
  create: (payload) => apiJson("/v1/orders", "POST", payload),
  confirmReservations: (payload) => apiJson("/v1/orders/confirm-reservations", "POST", payload),
  executeActions: (payload) => apiJson("/v1/orders/execute-actions", "POST", payload),
  delete: (id) => apiJson(`/v1/orders/${id}`, "DELETE"),
};

export const notificationApi = {
  sendSms: (payload) => apiJson("/v1/notifications/sms/send", "POST", payload),
};

export const chatApi = {
  replaceStep: (payload) => apiJson("/v1/chat/replace-step", "POST", payload),
};

export const memoryApi = {
  snapshot: () => apiGet("/v1/agent/memory"),
  clearSession: () => apiJson("/v1/agent/memory/session/clear", "POST"),
  // 聊天记录云同步
  pullChatSync: () => apiGet("/v1/agent/chat/sync"),
  pushChatSync: (data) => apiJson("/v1/agent/chat/sync", "POST", data),
};

export const shareApi = {
  createShare: (payload) => apiJson("/v1/share/route", "POST", payload),
  getShare: (shareId) => apiGet(`/v1/share/route/${encodeURIComponent(shareId)}`),
};

export async function streamChat(payload, handlers = {}) {
  const response = await fetch(endpoint("/v1/chat/send"), {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload),
  });

  if (!response.ok || !response.body) {
    await parseResponse(response);
    return null;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() || "";
    chunks.forEach((chunk) => {
      const line = chunk.split("\n").find((item) => item.startsWith("data:"));
      if (!line) return;
      try {
        const event = JSON.parse(line.replace(/^data:\s*/, ""));
        handlers.onEvent?.(event);
      } catch (error) {
        handlers.onError?.(error);
      }
    });
  }

  return true;
}
