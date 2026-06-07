export const qs = (selector, root = document) => root.querySelector(selector);
export const qsa = (selector, root = document) => [...root.querySelectorAll(selector)];

export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

export function toast(message) {
  const node = qs("#toast");
  node.textContent = readableMessage(message);
  node.classList.add("is-visible");
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(() => node.classList.remove("is-visible"), 2400);
}

export function readableMessage(value) {
  if (!value) return "";
  if (typeof value === "string") {
    return value === "[object Object]" ? "操作失败，请稍后重试" : value;
  }
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (typeof value === "object") {
    return readableMessage(value.message)
      || readableMessage(value.msg)
      || readableMessage(value.detail)
      || readableMessage(value.error)
      || "操作失败，请稍后重试";
  }
  return String(value);
}

export function formData(form) {
  return Object.fromEntries(new FormData(form).entries());
}

export function tagsHtml(tags = []) {
  return (Array.isArray(tags) ? tags : [])
    .map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`)
    .join("");
}

export function emptyState(text = "暂无数据") {
  return `<div class="empty-state"><strong>${escapeHtml(text)}</strong><span>稍后刷新或先创建一条记录。</span></div>`;
}

export function panel(title, body, action = "") {
  return `
    <section class="panel">
      <div class="panel__head">
        <h3>${escapeHtml(title)}</h3>
        ${action}
      </div>
      <div class="panel__body">${body}</div>
    </section>
  `;
}

export function setLoading(root, text = "正在加载") {
  root.innerHTML = `<div class="empty-state"><strong>${escapeHtml(text)}</strong><span>正在向后端请求数据。</span></div>`;
}

export function routeDataFromPlan(plan) {
  const steps = Array.isArray(plan?.steps) ? plan.steps : [];
  return steps.map((step) => ({
    place_name: step.name,
    time: step.time,
    people_count: plan?.profile?.people?.adults || 1,
    price: step.price || step.meta || "待结算",
  }));
}
