import { clearSession, setSession, state, pullChatFromServer } from "./state.js";
import { qs, qsa, toast } from "./ui.js";
import { userApi } from "./api.js";
import { bindAuth } from "./views/auth.js";
import { renderChat } from "./views/chat.js?v=20260608-214500";
import { renderFamily } from "./views/family.js?v=20260607-190000";
import { renderMemory } from "./views/memory.js";
import { renderOrders } from "./views/orders.js?v=20260607-210000";
import { renderProfile } from "./views/profile.js";
import { renderShowcase } from "./views/showcase.js";

const ROUTE_CACHE_VERSION = "20260601-placeimage";
const ROUTE_CACHE_VERSION_KEY = "brx.routeCacheVersion";

if (localStorage.getItem(ROUTE_CACHE_VERSION_KEY) !== ROUTE_CACHE_VERSION) {
  [
    "banrixian.chatState",
    "brx.chatConversations",
    "brx.activeConversationId",
  ].forEach((key) => localStorage.removeItem(key));
  sessionStorage.removeItem("brx.bookedPlaces");
  localStorage.setItem(ROUTE_CACHE_VERSION_KEY, ROUTE_CACHE_VERSION);
  state.chatMessages = [];
  state.lastPlan = null;
}

const renderers = {
  chat:     renderChat,
  orders:   renderOrders,
  family:   renderFamily,
  memory:   renderMemory,
  profile:  renderProfile,
  showcase: renderShowcase,
};

const authScreen = qs("#auth-screen");
const appShell   = qs("#app-shell");
const root       = qs("#view-root");

// ── Auth ──────────────────────────────────────────────
bindAuth(bootApp);

// ── Logout ────────────────────────────────────────────
function doLogout() {
  clearSession();
  appShell.classList.add("is-hidden");
  authScreen.classList.remove("is-hidden");
}

qs("#logout-button")?.addEventListener("click", doLogout);
qs("#logout-button-mobile")?.addEventListener("click", doLogout);

// ── Sign-in ───────────────────────────────────────────
async function doSign() {
  try {
    const response = await userApi.sign();
    toast(response.data?.msg || "签到成功 +50 积分");
    if (state.activeRoute === "profile") await navigate("profile");
  } catch (error) {
    toast(error.message);
  }
}

qs("#sign-button")?.addEventListener("click", doSign);
qs("#sign-button-mobile")?.addEventListener("click", doSign);

// ── View toggle (desktop / mobile) ───────────────────
const FORCE_KEY = "banrixian.forceView";
const savedForce = localStorage.getItem(FORCE_KEY);
if (savedForce === "mobile") document.body.classList.add("force-mobile");
if (savedForce === "desktop") document.body.classList.add("force-desktop");

function setForceView(mode) {
  document.body.classList.remove("force-mobile", "force-desktop");
  if (mode) {
    document.body.classList.add(`force-${mode}`);
    localStorage.setItem(FORCE_KEY, mode);
  } else {
    localStorage.removeItem(FORCE_KEY);
  }
  updateToggleLabels();
}

function isDesktopMode() {
  if (document.body.classList.contains("force-mobile")) return false;
  if (document.body.classList.contains("force-desktop")) return true;
  return window.matchMedia("(min-width: 768px)").matches;
}

function updateToggleLabels() {
  const desktop = isDesktopMode();
  const toggleDesktop = qs("#toggle-view");
  const toggleMobile  = qs("#toggle-view-mobile");
  if (toggleDesktop) toggleDesktop.textContent = desktop ? "切换手机版" : "切换桌面版";
  if (toggleMobile)  toggleMobile.textContent  = desktop ? "手机版" : "桌面版";
}

qs("#toggle-view")?.addEventListener("click", () => {
  if (isDesktopMode()) setForceView("mobile"); else setForceView("desktop");
});
qs("#toggle-view-mobile")?.addEventListener("click", () => {
  if (isDesktopMode()) setForceView("mobile"); else setForceView("desktop");
});

// ── Desktop banner dismiss ────────────────────────────
const BANNER_KEY = "banrixian.bannerDismissed";
if (localStorage.getItem(BANNER_KEY)) {
  const banner = qs("#desktop-banner");
  if (banner) banner.style.display = "none";
}
qs("#close-banner")?.addEventListener("click", () => {
  const banner = qs("#desktop-banner");
  if (banner) banner.style.display = "none";
  localStorage.setItem(BANNER_KEY, "1");
});

// ── Navigation ───────────────────────────────────────
qsa("[data-route]").forEach((button) => {
  button.addEventListener("click", () => navigate(button.dataset.route));
});

// ── 行程分享页（?share=xxx，无需登录）────────────────────────────────────────
const _shareId = new URLSearchParams(window.location.search).get("share");
if (_shareId) {
  // 隐藏登录和应用壳，展示分享页
  authScreen?.classList.add("is-hidden");
  appShell?.classList.add("is-hidden");
  const shareWrap = document.createElement("div");
  shareWrap.id = "share-view-root";
  shareWrap.style.cssText = "min-height:100vh;background:#f4f4f6";
  document.body.appendChild(shareWrap);
  import("./views/share.js?v=20260608-214500").then(({ renderShare }) => {
    renderShare(shareWrap, _shareId);
  }).catch((err) => {
    shareWrap.innerHTML = `<div style="padding:40px;text-align:center;color:#888;font-size:14px">加载失败：${String(err?.message || err)}</div>`;
  });
} else if (state.token) {
  bootApp();
}

async function bootApp() {
  authScreen.classList.add("is-hidden");
  appShell.classList.remove("is-hidden");
  updateToggleLabels();
  // 登录后后台拉取服务端聊天记录（不阻塞渲染）
  pullChatFromServer().catch(() => {});
  await navigate(state.activeRoute || "chat");
}

export async function navigate(route) {
  state.previousRoute = state.activeRoute; // 记录上一页
  state.activeRoute = route in renderers ? route : "chat";

  // Activate nav items in both sidebar and bottom tabs
  qsa("[data-route]").forEach((item) =>
    item.classList.toggle("is-active", item.dataset.route === state.activeRoute)
  );

  try {
    await renderers[state.activeRoute](root);
  } catch (error) {
    if (String(error.message).includes("401")) {
      setSession("", null);
      appShell.classList.add("is-hidden");
      authScreen.classList.remove("is-hidden");
      return;
    }
    root.innerHTML = `<div class="empty-state"><strong>加载失败</strong><span>${error.message}</span></div>`;
    toast(error.message);
  }
}

// ── 移动端 QR 弹窗 ────────────────────────────────────────────────────────────
const qrModal    = qs("#qr-mobile-modal");
const qrTrigger  = qs("#qr-mobile-trigger");
const qrClose    = qs("#qr-modal-close");
const qrBackdrop = qs("#qr-modal-backdrop");

function openQRModal() {
  qrModal?.classList.remove("is-hidden");
  // 触发 MutationObserver 初始化 QR
  if (typeof window.initQRCodes === "function") window.initQRCodes();
}
function closeQRModal() { qrModal?.classList.add("is-hidden"); }

qrTrigger?.addEventListener("click", openQRModal);
qrClose?.addEventListener("click", closeQRModal);
qrBackdrop?.addEventListener("click", closeQRModal);
