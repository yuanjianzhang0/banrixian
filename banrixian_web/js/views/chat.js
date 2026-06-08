/**
 * chat.js — 规划 + 聊天主视图
 *
 * 关键设计：
 * - 模块级 _ps（planning session）保持规划状态，切换页面再回来能恢复
 * - 默认"聊天模式"（简单输入框）；可切换"规划表单模式"
 * - SSE done 事件结构：{ type:"done", data:{ type:"plan"|"text", ... } }
 */

import { streamChat, orderApi, chatApi, contentApi, shareApi } from "../api.js?v=20260607-210000";
import { state, addChatMessage, setLastPlan, clearChatState } from "../state.js";
import { qs, qsa, escapeHtml, toast, routeDataFromPlan } from "../ui.js";
import { SKILL_LABELS, EXAMPLE_QUERIES, QUICK_CHIPS, routeStyles } from "../config.js";
import { Icon } from "../icons.js";

// ─── 模块级规划状态（页面重渲染后可恢复）─────────────────────────────────────
const _ps = {
  active:      false,      // 是否正在规划
  steps:       null,       // thinkingState[]
  tc:          null,       // #thinking-container DOM 元素
  rc:          null,       // #result-container DOM 元素
  finalPlan:   null,       // 最终方案（用于恢复显示）
  doneText:    null,       // 普通文字回复
  userText:    "",         // 用户原始提问（用作行程名称）
  bookedSteps: new Set(),  // 已预约的 step 索引
  routeHidden: false,
};

// 思考步骤顺序
const STEP_ORDER = [
  "get_current_time", "decompose_goal", "get_memory", "get_weather",
  "analyze_user_profile", "plan_time_slots", "search_places",
  "rank_places_for_plan", "score_plans", "build_final_plan",
];
const DEFAULT_STEP_ORDER = STEP_ORDER.filter((skill) => skill !== "get_weather");
const CITY_OPTIONS = ["北京", "上海", "广州", "深圳", "杭州", "成都", "南京", "武汉", "西安", "重庆", "天津"];
const CITY_KEY = "brx.selectedCity";
const ORDER_CITY_KEY = "brx.ordersCity";

let _amapInstance = null;  // 高德地图实例（替换 Leaflet）
let _mode = "chat"; // "chat" | "form"

function _currentCity() {
  const saved = localStorage.getItem(CITY_KEY);
  return saved || state.user?.city || "北京";
}

function _extractPhone(step) {
  const raw = String(step?.phone || step?.meta || "");
  const match = raw.match(/(?:电话\s*)?((?:\+?86[-\s]?)?(?:0\d{2,3}[-\s]?\d{7,8}|1[3-9]\d{9})(?:[;；,，/]\s*(?:0\d{2,3}[-\s]?\d{7,8}|1[3-9]\d{9}))*)/);
  return match ? match[1].split(/[;；,，/]/)[0].trim() : "";
}

function _phoneHref(phone) {
  return `tel:${String(phone || "").replace(/[^\d+]/g, "")}`;
}

function _cityOptionsHtml() {
  const current = _currentCity();
  return CITY_OPTIONS.map((city) =>
    `<option value="${escapeHtml(city)}"${city === current ? " selected" : ""}>${escapeHtml(city)}</option>`
  ).join("");
}

// 当前会话气泡列表（模块级，切换 tab 回来可恢复，但不预加载全部历史）
let _sessionBubbles = []; // [{role, content}]

// ─── 已预约地点名称集合（sessionStorage 备份，tab 切换和刷新都不丢）─────────
const _BOOKED_KEY = "brx.bookedPlaces";
const _CONVERSATIONS_KEY = "brx.chatConversations";
const _ACTIVE_CONVERSATION_KEY = "brx.activeConversationId";
let _bookedPlaceNames = (() => {
  try { return new Set(JSON.parse(sessionStorage.getItem(_BOOKED_KEY) || "[]")); }
  catch { return new Set(); }
})();
function _persistBooked() {
  try { sessionStorage.setItem(_BOOKED_KEY, JSON.stringify([..._bookedPlaceNames])); }
  catch {}
}

function _clearBookedFlagInPlan(plan, placeName) {
  if (!plan || !Array.isArray(plan.steps)) return false;
  let changed = false;
  plan.steps.forEach((step) => {
    if (step?.name === placeName && step.booked === true) {
      delete step.booked;
      changed = true;
    }
  });
  return changed;
}

export function unlockBookedPlace(placeName) {
  const name = String(placeName || "").trim();
  if (!name) return;

  _bookedPlaceNames.delete(name);
  _persistBooked();

  _clearBookedFlagInPlan(_ps.finalPlan, name);
  _clearBookedFlagInPlan(state.lastPlan, name);
  (state.chatMessages || []).forEach((msg) => _clearBookedFlagInPlan(msg.planSnapshot, name));
  _ps.bookedSteps = new Set(
    [..._ps.bookedSteps].filter((idx) => _ps.finalPlan?.steps?.[idx]?.name !== name)
  );
  setLastPlan(_ps.finalPlan || state.lastPlan);

  _conversations.forEach((conv) => {
    _clearBookedFlagInPlan(conv.lastPlan, name);
    Object.values(conv.planSnapshots || {}).forEach((plan) => _clearBookedFlagInPlan(plan, name));
    (conv.messages || []).forEach((msg) => _clearBookedFlagInPlan(msg.planSnapshot, name));
  });
  _saveConversations();
}

let _conversations = _loadConversations();
let _activeConversationId = localStorage.getItem(_ACTIVE_CONVERSATION_KEY) || _conversations[0]?.id || "";

function _newConversation(title = "新对话") {
  const city = _currentCity();
  return {
    id: `conv_${Date.now()}_${Math.random().toString(16).slice(2, 8)}`,
    title,
    city,
    messages: [],
    lastPlan: null,
    planSnapshots: {},
    thinkingSteps: null,
    routeHidden: false,
    updatedAt: Date.now(),
  };
}

function _loadConversations() {
  try {
    const rows = JSON.parse(localStorage.getItem(_CONVERSATIONS_KEY) || "[]");
    if (Array.isArray(rows) && rows.length) return rows;
  } catch {}
  const seed = _newConversation("当前对话");
  seed.city = _currentCity();
  seed.messages = Array.isArray(state.chatMessages) ? state.chatMessages.slice(-20) : [];
  seed.lastPlan = state.lastPlan || null;
  seed.planSnapshots = seed.lastPlan ? { restored: seed.lastPlan } : {};
  return [seed];
}

function _saveConversations() {
  _conversations = _conversations
    .map((c) => ({ ...c, messages: (c.messages || []).slice(-60) }))
    .sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0))
    .slice(0, 12);
  localStorage.setItem(_CONVERSATIONS_KEY, JSON.stringify(_conversations));
  localStorage.setItem(_ACTIVE_CONVERSATION_KEY, _activeConversationId);
}

function _activeConversation() {
  let conv = _conversations.find((item) => item.id === _activeConversationId);
  if (!conv) {
    conv = _newConversation("新对话");
    _conversations.unshift(conv);
    _activeConversationId = conv.id;
    _saveConversations();
  }
  return conv;
}

function _resetPlanningState() {
  _ps.active = false;
  _ps.finalPlan = null;
  _ps.doneText = null;
  _ps.steps = null;
  _ps.routeHidden = false;
  _sessionBubbles = [];
}

function _createConversationForCity(city) {
  const normalizedCity = city || "北京";
  localStorage.setItem(CITY_KEY, normalizedCity);
  localStorage.setItem(ORDER_CITY_KEY, normalizedCity);
  const conv = _newConversation(`${normalizedCity} · 新对话`);
  conv.city = normalizedCity;
  _conversations.unshift(conv);
  _activeConversationId = conv.id;
  _resetPlanningState();
  _saveConversations();
  return conv;
}

function _switchCity(root, city) {
  const nextCity = city || "北京";
  const prevCity = _currentCity();
  if (nextCity === prevCity) return;
  if (_ps.active) {
    toast("当前正在规划中，请稍候");
    qsa("#plan-city, #form-city", root).forEach((item) => { item.value = prevCity; });
    return;
  }
  _createConversationForCity(nextCity);
  toast(`已切换到${nextCity}，并新建对话`);
  renderChat(root);
}

function _hydrateConversationState() {
  const conv = _activeConversation();
  if (conv.city) localStorage.setItem(CITY_KEY, conv.city);
  _sessionBubbles = (conv.messages || [])
    .filter((msg) => msg.role === "user" || msg.role === "assistant")
    .map((msg) => ({
      role: msg.role,
      content: String(msg.content || ""),
      planId: msg.planId || "",
      planSnapshot: msg.planSnapshot || null,
    }));
  _ps.finalPlan = conv.lastPlan || null;
  _ps.steps = Array.isArray(conv.thinkingSteps) ? conv.thinkingSteps : _ps.steps;
  _ps.routeHidden = conv.routeHidden === true;
}

function _persistActiveConversation() {
  const conv = _activeConversation();
  conv.city = conv.city || _currentCity();
  conv.messages = _sessionBubbles.map((msg) => ({
    role: msg.role,
    content: msg.content,
    planId: msg.planId || "",
    planSnapshot: msg.planSnapshot || null,
  }));
  conv.lastPlan = _ps.finalPlan || null;
  conv.thinkingSteps = _ps.steps || null;
  conv.routeHidden = _ps.routeHidden === true;
  conv.planSnapshots = conv.planSnapshots || {};
  conv.updatedAt = Date.now();
  const firstUser = conv.messages.find((msg) => msg.role === "user")?.content || "";
  if (firstUser) conv.title = firstUser.slice(0, 18);
  _saveConversations();
}

function _showPlanSnapshot(root, planId, content = "") {
  const conv = _activeConversation();
  const plan = conv.planSnapshots?.[planId]
    || (conv.messages || []).find((msg) => msg.planId === planId)?.planSnapshot
    || (conv.lastPlan?._planId === planId ? conv.lastPlan : null);
  const fallbackPlan = plan || (
    !planId && content && conv.lastPlan?.intro && String(content).includes(String(conv.lastPlan.intro).slice(0, 24))
      ? conv.lastPlan
      : null
  );
  if (!fallbackPlan) {
    toast("这条路线记录已不可用");
    return;
  }

  _ps.finalPlan = fallbackPlan;
  _ps.routeHidden = false;
  conv.lastPlan = fallbackPlan;
  conv.routeHidden = false;
  conv.updatedAt = Date.now();
  _saveConversations();

  const rc = qs("#result-container", root);
  if (!rc) {
    renderChat(root);
    return;
  }
  _renderPlan(root, fallbackPlan);
  rc.scrollIntoView({ behavior: "smooth", block: "start" });
}

// ─── SSE 驱动的思考过程 ──────────────────────────────────────────────────────
let   _thinkTimer   = null;
const _pendingObs   = {};   // skill → obs text（SSE observation 到达时写入）

function _startThinkSequence() {
  clearTimeout(_thinkTimer);
  _thinkTimer = null;
  Object.keys(_pendingObs).forEach((k) => delete _pendingObs[k]);
}

function _ensureThinkingStep(skill) {
  if (!skill || !_ps.steps) return null;
  const existing = _ps.steps.find((s) => s.skill === skill);
  if (existing) return existing;

  const step = {
    skill,
    label: SKILL_LABELS[skill] || skill,
    status: "pending",
    obs: "",
  };
  const orderIndex = STEP_ORDER.indexOf(skill);
  let insertAt = _ps.steps.length;
  if (orderIndex >= 0) {
    const laterIndex = _ps.steps.findIndex((item) => {
      const itemIndex = STEP_ORDER.indexOf(item.skill);
      return itemIndex >= 0 && itemIndex > orderIndex;
    });
    if (laterIndex >= 0) insertAt = laterIndex;
  }
  _ps.steps.splice(insertAt, 0, step);

  if (_ps.tc && document.contains(_ps.tc)) {
    const stepsEl = qs(".thinking-steps", _ps.tc);
    if (stepsEl) stepsEl.innerHTML = _ps.steps.map(_renderStep).join("");
  }
  return step;
}

function _setThinkingStep(skill, status, obs = "") {
  if (!skill || !_ps.steps) return null;
  const step = _ensureThinkingStep(skill);
  if (!step) return null;
  step.status = status || step.status;
  if (obs) step.obs = obs;
  _updateStepDOM(step);
  const el = _ps.tc ? qs(`[data-skill="${skill}"]`, _ps.tc) : null;
  if (status === "active") el?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  _persistActiveConversation();
  return step;
}

function _stopThinkSequence({ success = false, finalPlan = null, textReply = "" } = {}) {
  if (_thinkTimer) { clearTimeout(_thinkTimer); _thinkTimer = null; }

  const routeCount = Array.isArray(finalPlan?.steps) ? finalPlan.steps.length : 0;
  if (success && routeCount > 0) {
    _setThinkingStep("build_final_plan", "done", `已生成路线：${routeCount} 个地点`);
  }

  (_ps.steps || []).forEach((s) => {
    if (s.status === "active") {
      s.status = success ? "done" : "skipped";
    } else if (s.status === "pending") {
      s.status = "skipped";
    }
    if (_pendingObs[s.skill]) s.obs = _pendingObs[s.skill];
    if (!s.obs && s.status === "skipped") s.obs = success ? "本次未触发该步骤" : (textReply || "本次未返回该步骤结果");
    _updateStepDOM(s);
  });
  _persistActiveConversation();
}

// ─── 主入口 ──────────────────────────────────────────────────────────────────
export async function renderChat(root) {
  _hydrateConversationState();

  root.innerHTML = _buildHTML();
  _bindEvents(root);

  // 恢复当前会话气泡
  if (_sessionBubbles.length) {
    const hisEl = qs("#chat-history", root);
    if (hisEl) {
      _sessionBubbles.forEach((b) => {
        hisEl.appendChild(_makeBubbleNode(b.role, b.content, b.planId || ""));
      });
      // 有气泡则隐藏标题，示例保留在输入区上方，方便连续提问时继续点选。
      qs("#chat-header", root)?.classList.add("is-hidden");
    }
  }

  // 恢复进行中的规划状态
  if (_ps.active && _ps.steps) {
    const tc = qs("#thinking-container", root);
    if (tc) {
      tc.innerHTML = _renderThinkingPanel(_ps.steps);
      tc.classList.remove("is-hidden");
      _ps.tc = tc;
    }
    const rc = qs("#result-container", root);
    if (rc) _ps.rc = rc;
    if (_ps.finalPlan && !_ps.routeHidden) _renderPlan(root, _ps.finalPlan);
  } else if (_ps.finalPlan) {
    const rc = qs("#result-container", root);
    if (rc) _ps.rc = rc;
    if (!_ps.routeHidden) _renderPlan(root, _ps.finalPlan);
  }
}

// ─── HTML 构建 ───────────────────────────────────────────────────────────────
function _buildHTML() {
  const examplesHtml = EXAMPLE_QUERIES.map((q) =>
    `<button class="example-pill" data-example="${escapeHtml(q)}">${escapeHtml(q)}</button>`
  ).join("");
  const historyHtml = _conversations.map((conv) => `
    <button class="conversation-tab ${conv.id === _activeConversationId ? "is-active" : ""}" data-conversation-id="${escapeHtml(conv.id)}">
      <span class="conversation-tab__city">${escapeHtml(conv.city || "北京")}</span>
      <span class="conversation-tab__title">${escapeHtml(conv.title || "新对话")}</span>
      <span class="conversation-tab__close" data-close-conversation="${escapeHtml(conv.id)}">×</span>
    </button>
  `).join("");

  return `
<div class="planner-wrap" id="planner-wrap">
  <section class="chat-workbench">
    <div class="conversation-bar">
      <div class="conversation-tabs" id="conversation-tabs">${historyHtml}</div>
      <label class="city-picker" title="选择本次规划城市">
        <span class="city-picker__meta">半日闲</span>
        <select id="plan-city">${_cityOptionsHtml()}</select>
        <span class="city-picker__slogan">安排好这一程</span>
      </label>
      <button id="new-conversation" class="btn btn--outline btn--sm" type="button">新建对话</button>
    </div>

    <!-- 标题（有气泡后隐藏）-->
    <div id="chat-header">
      <h2 class="planner-heading">半日闲 AI</h2>
      <p class="planner-subtext">你的本地出行助手，一句话帮你规划路线</p>
    </div>

    <!-- 对话气泡区（固定高度，内部滚动）-->
    <div class="chat-history" id="chat-history"></div>

    <!-- 示例横滑：连续对话后也保留，方便快速发起新需求 -->
    <div class="example-scroll" id="example-scroll">${examplesHtml}</div>

    <!-- 输入区：紧跟在对话气泡下方 -->
    <div id="chat-input-dock" class="chat-input-dock">
      <div id="chat-mode-area" class="planner-input-card ${_mode === "form" ? "is-hidden" : ""}">
        <div class="input-row">
          <textarea id="chat-text" class="plan-textarea"
            placeholder="例：今天下午带孩子出去玩，孩子5岁，别太远，帮我安排一下"
            rows="2" maxlength="500"
          ></textarea>
          <button id="chat-send" class="btn btn--primary" style="height:52px;width:64px;border-radius:var(--radius-sm);flex-shrink:0">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M22 2L11 13"/><path d="M22 2L15 22 11 13 2 9l20-7z"/></svg>
          </button>
        </div>
        <div class="chip-row">
          ${QUICK_CHIPS.map((c) => `<button class="chip" data-chip="${escapeHtml(c)}">${escapeHtml(c)}</button>`).join("")}
        </div>
        <div style="display:flex;justify-content:space-between;align-items:center">
          <button id="toggle-form" class="btn btn--ghost btn--sm" type="button">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="flex-shrink:0"><line x1="4" y1="6" x2="20" y2="6"/><line x1="4" y1="12" x2="20" y2="12"/><line x1="4" y1="18" x2="20" y2="18"/><circle cx="9" cy="6" r="2.5" fill="currentColor" stroke="none"/><circle cx="15" cy="12" r="2.5" fill="currentColor" stroke="none"/><circle cx="9" cy="18" r="2.5" fill="currentColor" stroke="none"/></svg>
            高级选项
          </button>
          <button id="clear-history" class="btn btn--ghost btn--sm" type="button">清空当前</button>
        </div>
      </div>

      <div id="form-mode-area" class="planner-input-card ${_mode === "chat" ? "is-hidden" : ""}">
        <label class="field-label">
          <span>告诉我你的想法</span>
          <textarea id="form-text" class="plan-textarea" rows="3"
            placeholder="例：今天下午我和老婆孩子出去玩，孩子5岁，别太远，帮我安排一下"
            maxlength="500"
          ></textarea>
        </label>
        <div class="style-row">
          <label class="field-label location-field">
            <span>商圈 / 区域（可选）</span>
            <input id="form-location" type="text" placeholder="例：朝阳、三里屯" />
          </label>
          <label class="field-label">
            <span>城市</span>
            <select id="form-city">${_cityOptionsHtml()}</select>
          </label>
          <label class="field-label">
            <span>出行风格</span>
            <select id="form-style">
              <option value="">不限</option>
              ${routeStyles.map((s) => `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`).join("")}
            </select>
          </label>
        </div>
        <div class="chip-row">
          ${QUICK_CHIPS.map((c) => `<button class="chip form-chip" data-chip="${escapeHtml(c)}">${escapeHtml(c)}</button>`).join("")}
        </div>
        <button id="form-submit" class="btn btn--primary btn--lg btn--wide">开始规划</button>
        <button id="toggle-chat" class="btn btn--ghost btn--sm" type="button">← 返回聊天</button>
      </div>
    </div>
  </section>

  <!-- 思考过程临时容器：规划中显示，生成后融合到路线卡片 -->
  <div id="thinking-container" class="is-hidden"></div>

  <!-- 路线结果 -->
  <div id="result-container"></div>

</div>`;
}

/** 创建单条气泡 DOM 节点 */
function _makeBubbleNode(role, content, planId = "") {
  const txt = String(content || "");
  const short = txt.slice(0, 300);
  const truncated = txt.length > 300;
  const row = document.createElement("div");
  if (role === "user") {
    row.className = "bubble-row bubble-row--user";
    row.innerHTML = `<div class="bubble bubble--user">${escapeHtml(short)}${truncated ? "…" : ""}</div>`;
  } else {
    row.className = "bubble-row bubble-row--ai";
    const planAttrs = planId ? ` data-plan-id="${escapeHtml(planId)}" title="点击查看这次路线"` : "";
    const planClass = planId ? " bubble--plan-link" : "";
    row.innerHTML = `<div class="bubble-avatar">AI</div><div class="bubble bubble--ai${planClass}"${planAttrs}>${escapeHtml(short)}${truncated ? "…" : ""}</div>`;
  }
  return row;
}

/** 追加气泡到对话区，同时隐藏首页标题 */
function _appendBubble(root, role, content, planId = "") {
  const hisEl = qs("#chat-history", root) || qs("#chat-history");
  if (!hisEl) return;
  const node = _makeBubbleNode(role, content, planId);
  hisEl.appendChild(node);
  hisEl.scrollTo({ top: hisEl.scrollHeight, behavior: "smooth" });
  // 第一条消息出现后，只隐藏标题；示例提示继续保留。
  qs("#chat-header", root || document)?.classList.add("is-hidden");
}

// ─── 事件绑定 ─────────────────────────────────────────────────────────────────
function _bindEvents(root) {
  if (!root.dataset.chatDelegated) {
    root.dataset.chatDelegated = "1";
    root.addEventListener("click", (event) => {
      const planBubble = event.target.closest(".bubble--plan-link");
      if (planBubble && root.contains(planBubble)) {
        _showPlanSnapshot(root, planBubble.dataset.planId || "", planBubble.textContent || "");
        return;
      }

      const closeBtn = event.target.closest(".conversation-tab__close");
      if (closeBtn && root.contains(closeBtn)) {
        event.stopPropagation();
        if (_ps.active) { toast("当前正在规划中，请稍候"); return; }
        const id = closeBtn.dataset.closeConversation || "";
        _conversations = _conversations.filter((conv) => conv.id !== id);
        if (!_conversations.length) _conversations.push(_newConversation("新对话"));
        if (_activeConversationId === id) _activeConversationId = _conversations[0].id;
        _saveConversations();
        renderChat(root);
        return;
      }

      const tab = event.target.closest(".conversation-tab");
      if (tab && root.contains(tab)) {
        if (_ps.active) { toast("当前正在规划中，请稍候"); return; }
        _activeConversationId = tab.dataset.conversationId || _activeConversationId;
        const conv = _activeConversation();
        if (conv.city) localStorage.setItem(CITY_KEY, conv.city);
        localStorage.setItem(_ACTIVE_CONVERSATION_KEY, _activeConversationId);
        renderChat(root);
      }
    });
  }

  /*
  qsa(".conversation-tab", root).forEach((btn) => {
    btn.addEventListener("click", (event) => {
      if (_ps.active) { toast("当前正在规划中，请稍候"); return; }
      if (event?.target?.closest?.(".conversation-tab__close")) return;
      _activeConversationId = btn.dataset.conversationId || _activeConversationId;
      localStorage.setItem(_ACTIVE_CONVERSATION_KEY, _activeConversationId);
      renderChat(root);
    });
  });

  qsa(".conversation-tab__close", root).forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.stopPropagation();
      if (_ps.active) { toast("当前正在规划中，请稍候"); return; }
      const id = btn.dataset.closeConversation || "";
      _conversations = _conversations.filter((conv) => conv.id !== id);
      if (!_conversations.length) _conversations.push(_newConversation("新对话"));
      if (_activeConversationId === id) _activeConversationId = _conversations[0].id;
      _saveConversations();
      renderChat(root);
    });
  });
  */

  qs("#new-conversation", root)?.addEventListener("click", () => {
    if (_ps.active) { toast("当前正在规划中，请稍候"); return; }
    const city = _currentCity();
    const conv = _newConversation(`${city} · 新对话`);
    conv.city = city;
    _conversations.unshift(conv);
    _activeConversationId = conv.id;
    _resetPlanningState();
    _saveConversations();
    renderChat(root);
  });

  // 示例 pills
  qsa(".example-pill", root).forEach((btn) => {
    btn.addEventListener("click", () => {
      const ta = qs(_mode === "chat" ? "#chat-text" : "#form-text", root);
      if (ta) { ta.value = btn.dataset.example; ta.focus(); }
    });
  });

  // 聊天 chips
  qsa(".chip:not(.form-chip)", root).forEach((chip) => {
    chip.addEventListener("click", () => {
      chip.classList.toggle("is-active");
      const ta = qs("#chat-text", root);
      if (ta && chip.classList.contains("is-active")) {
        const sep = ta.value.trim() ? "，" : "";
        ta.value = ta.value.trim() + sep + chip.dataset.chip;
      }
    });
  });

  // 表单 chips
  qsa(".form-chip", root).forEach((chip) => {
    chip.addEventListener("click", () => chip.classList.toggle("is-active"));
  });

  qsa("#plan-city, #form-city", root).forEach((select) => {
    select.addEventListener("change", () => {
      const city = select.value || "北京";
      _switchCity(root, city);
    });
  });

  // 聊天发送
  qs("#chat-send", root)?.addEventListener("click", () => _submitChat(root));
  qs("#chat-text", root)?.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); _submitChat(root); }
  });

  // 规划表单发送
  qs("#form-submit", root)?.addEventListener("click", () => _submitForm(root));
  qs("#form-text", root)?.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); _submitForm(root); }
  });

  // 模式切换
  qs("#toggle-form", root)?.addEventListener("click", () => {
    _mode = "form";
    qs("#chat-mode-area", root)?.classList.add("is-hidden");
    qs("#form-mode-area", root)?.classList.remove("is-hidden");
    qs("#form-text", root)?.focus();
  });
  qs("#toggle-chat", root)?.addEventListener("click", () => {
    _mode = "chat";
    qs("#form-mode-area", root)?.classList.add("is-hidden");
    qs("#chat-mode-area", root)?.classList.remove("is-hidden");
    qs("#chat-text", root)?.focus();
  });

  // 清空历史
  qs("#clear-history", root)?.addEventListener("click", () => {
    clearChatState();
    _sessionBubbles = [];
    _ps.active      = false;
    _ps.finalPlan   = null;
    _ps.doneText    = null;
    _ps.steps       = null;
    _ps.bookedSteps = new Set();
    _bookedPlaceNames = new Set();
    _persistBooked();
    const conv = _activeConversation();
    conv.messages = [];
    conv.lastPlan = null;
    conv.thinkingSteps = null;
    conv.title = "新对话";
    conv.updatedAt = Date.now();
    _saveConversations();
    renderChat(root);
    toast("已清空当前对话");
  });
}

// ─── 提交入口 ─────────────────────────────────────────────────────────────────
async function _submitChat(root) {
  const ta = qs("#chat-text", root);
  const text = ta?.value?.trim();
  if (!text) { toast("请输入你的想法"); return; }
  ta.value = "";
  await _startPlan(root, text);
}

async function _submitForm(root) {
  const text = qs("#form-text", root)?.value?.trim();
  if (!text) { toast("请输入出行需求"); return; }

  const location = qs("#form-location", root)?.value?.trim() || "";
  const city     = qs("#form-city", root)?.value || _currentCity();
  const style    = qs("#form-style", root)?.value || "";
  const chips    = qsa(".form-chip.is-active", root).map((c) => c.dataset.chip);

  let fullText = text;
  if (city) fullText += `；城市：${city}`;
  if (location) fullText += `；区域：${location}`;
  if (style)    fullText += `；风格：${style}`;
  if (chips.length) fullText += `；偏好：${chips.join("、")}`;

  await _startPlan(root, fullText);
}

// ─── 核心规划流程 ─────────────────────────────────────────────────────────────
async function _startPlan(root, text) {
  if (_ps.active) { toast("当前正在规划中，请稍候"); return; }

  // 立即追加用户气泡
  _sessionBubbles.push({ role: "user", content: text });
  _appendBubble(root, "user", text);
  addChatMessage({ role: "user", content: text });
  _persistActiveConversation();

  // 初始化规划状态
  _ps.active      = true;
  _ps.userText    = text;
  _ps.steps       = _buildThinkingState();
  _ps.finalPlan   = null;
  _ps.doneText    = null;
  _ps.bookedSteps = new Set();
  _ps.routeHidden = false;
  _bookedPlaceNames = new Set();  // 新规划开始时清空预约记录
  _persistBooked();

  // 清空旧结果
  const rc = qs("#result-container", root);
  if (rc) { rc.innerHTML = ""; _ps.rc = rc; }

  // 展示思考面板并启动计时器动画
  const tc = qs("#thinking-container", root);
  if (tc) {
    tc.innerHTML = _renderThinkingPanel(_ps.steps);
    tc.classList.remove("is-hidden");
    _ps.tc = tc;
  }
  _startThinkSequence();

  // 禁用发送按钮
  const sendBtn = qs("#chat-send", root);
  const formBtn = qs("#form-submit", root);
  if (sendBtn) sendBtn.disabled = true;
  if (formBtn) { formBtn.disabled = true; formBtn.textContent = "规划中…"; }

  const city = qs("#plan-city", root)?.value || qs("#form-city", root)?.value || _currentCity();
  localStorage.setItem(CITY_KEY, city);
  _activeConversation().city = city;

  try {
    await streamChat({ text, city }, {
      onEvent: (event) => _handleSSE(event),
      onError: (err) => console.warn("SSE parse err", err),
    });
  } catch (err) {
    toast(err.message || "连接失败，请重试");
    _ps.active = false;
  } finally {
    if (sendBtn) sendBtn.disabled = false;
    if (formBtn) { formBtn.disabled = false; formBtn.textContent = "开始规划"; }
  }

  const inDOM = !!qs("#result-container");

  const hasUsablePlan = _ps.finalPlan
    && Array.isArray(_ps.finalPlan.steps)
    && _ps.finalPlan.steps.length > 0;

  if (hasUsablePlan) {
    _stopThinkSequence({ success: true, finalPlan: _ps.finalPlan });
    _updateStatusText("完成");
    const planId = `plan_${Date.now()}_${Math.random().toString(16).slice(2, 8)}`;
    _ps.finalPlan = { ..._ps.finalPlan, _planId: planId };
    const conv = _activeConversation();
    conv.planSnapshots = conv.planSnapshots || {};
    conv.planSnapshots[planId] = _ps.finalPlan;
    conv.lastPlan = _ps.finalPlan;
    conv.routeHidden = false;
    setLastPlan(_ps.finalPlan);
    const aiMsg = _ps.finalPlan.intro || "已生成路线";
    addChatMessage({ role: "assistant", content: aiMsg });
    // AI 气泡：简短 intro
    _sessionBubbles.push({ role: "assistant", content: aiMsg, planId, planSnapshot: _ps.finalPlan });
    if (inDOM) {
      _appendBubble(root, "assistant", aiMsg, planId);
      _renderPlan(root, _ps.finalPlan);
      _collapseThinking();
    }
  } else if (_ps.finalPlan) {
    const fallbackText = _ps.finalPlan.intro || "这次没有找到可执行的真实路线，请换个城市、时间或偏好再试一次。";
    _ps.doneText = fallbackText;
    _ps.finalPlan = null;
    _stopThinkSequence({ success: false, textReply: fallbackText });
    _updateStatusText("未生成可用路线");
    addChatMessage({ role: "assistant", content: fallbackText });
    _sessionBubbles.push({ role: "assistant", content: fallbackText });
    if (inDOM) {
      _appendBubble(root, "assistant", fallbackText);
      _renderTextReply(root, fallbackText);
    }
  } else if (_ps.doneText) {
    _stopThinkSequence({ success: false, textReply: _ps.doneText });
    _updateStatusText("已回复");
    addChatMessage({ role: "assistant", content: _ps.doneText });
    _sessionBubbles.push({ role: "assistant", content: _ps.doneText });
    if (inDOM) {
      _appendBubble(root, "assistant", _ps.doneText);
      _renderTextReply(root, _ps.doneText);
      _ps.tc?.classList.add("is-hidden");
    }
  }

  _ps.active = false;
  _persistActiveConversation();
}

// ─── SSE 事件处理 ─────────────────────────────────────────────────────────────
function _handleSSE(event) {
  if (!event?.type) return;

  // skill_call / observation 直接驱动思考步骤，避免动画伪完成
  if (event.type === "skill_call") {
    const skill = event.skill || event.skill_name || "";
    _setThinkingStep(skill, "active");
    _updateStatusText(SKILL_LABELS[skill] ? `正在${SKILL_LABELS[skill]}…` : "规划中…");
  }

  if (event.type === "observation") {
    const skill   = event.skill || event.skill_name || "";
    const summary = event.summary || "";
    const data    = event.data || {};
    if (data.ok === false) return;   // 失败的 observation 不展示
    const obs = _buildObsText(event, summary).slice(0, 160);
    if (obs && skill) {
      _pendingObs[skill] = obs;
      // 如果计时器已经完成了这步（状态=done），立刻更新 obs 文本
      const step = (_ps.steps || []).find((s) => s.skill === skill);
      if (step) {
        _setThinkingStep(skill, "done", obs);
      }
    }
  }

  if (event.type === "status") {
    _updateStatusText(event.content || "");
  }

  if (event.type === "done") {
    const payload = event.data || {};
    if (payload.type === "plan") {
      _ps.finalPlan = payload;
    } else {
      _ps.doneText = payload.content || payload.message || JSON.stringify(payload);
    }
  }

  if (event.type === "error") {
    toast(event.content || event.message || "规划出错");
  }
}

function _buildObsText(event, summary) {
  // 尽量从 observation data 里提取更多信息
  const data = event.data || {};
  const result = data.result || {};
  // 工具调用失败时只显示"已完成"，避免用户看到报错
  if (data.ok === false) return "已完成";
  const parts = [summary];

  if (result.readable) parts.push(result.readable); // get_current_time
  if (result.city && result.temperature_c != null)
    parts.push(`${result.city} ${result.weather || ""} ${result.temperature_c}℃`);
  if (result.count != null) parts.push(`找到 ${result.count} 个候选`);
  if (result.start && result.end) parts.push(`${result.start}–${result.end}`);
  if (Array.isArray(result.warnings) && result.warnings.length)
    parts.push(result.warnings[0]);
  if (result.scene?.length) parts.push(`场景：${result.scene.join("、")}`);

  return parts.filter(Boolean).join("  ·  ");
}

// ─── 思考面板 DOM ─────────────────────────────────────────────────────────────
function _buildThinkingState() {
  return DEFAULT_STEP_ORDER.map((skill) => ({
    skill, label: SKILL_LABELS[skill] || skill,
    status: "pending", obs: "",
  }));
}

function _renderThinkingPanel(steps) {
  return `
    <div class="thinking-panel">
      <div class="thinking-panel__head">
        <div class="thinking-panel__title-row">
          <span class="thinking-icon">${Icon.sparkle}</span>
          <span class="thinking-panel__title">AI 思考过程</span>
        </div>
        <span id="thinking-status" class="thinking-status">规划中…</span>
      </div>
      <div class="thinking-steps" id="thinking-steps">
        ${steps.map(_renderStep).join("")}
      </div>
    </div>`;
}

function _renderStep(step) {
  let icon;
  if (step.status === "done") {
    icon = `<span class="step-icon step-icon--done">✓</span>`;
  } else if (step.status === "active") {
    // 激活中：品牌色圈圈，快速旋转
    icon = `<span class="step-icon step-icon--active"><span class="spinner-step spinner-step--active"></span></span>`;
  } else if (step.status === "skipped") {
    icon = `<span class="step-icon step-icon--skipped">—</span>`;
  } else {
    // 等待中：灰色圈圈，慢速旋转
    icon = `<span class="step-icon step-icon--pending"><span class="spinner-step spinner-step--pending"></span></span>`;
  }

  return `
    <div class="thinking-step thinking-step--${step.status}" data-skill="${escapeHtml(step.skill)}">
      ${icon}
      <div class="thinking-step__body">
        <div class="thinking-step__label">${escapeHtml(step.label)}</div>
        ${step.obs ? `<div class="thinking-step__obs">${escapeHtml(step.obs)}</div>` : ""}
      </div>
    </div>`;
}

function _updateStepDOM(step) {
  if (!_ps.tc || !document.contains(_ps.tc)) return;
  qsa(`[data-skill="${step.skill}"]`, _ps.tc).forEach((old) => {
    const tmp = document.createElement("div");
    tmp.innerHTML = _renderStep(step);
    old.replaceWith(tmp.firstElementChild);
  });
}

function _updateStatusText(text) {
  const el = _ps.tc ? qs("#thinking-status", _ps.tc) : null;
  if (el && text) el.textContent = text;
}

function _markAllDone() {
  _stopThinkSequence();
}

function _collapseThinking() {
  if (!_ps.tc) return;
  const panel  = qs(".thinking-panel", _ps.tc);
  const stepsEl = qs(".thinking-steps", _ps.tc);
  const headEl  = qs(".thinking-panel__head", _ps.tc);
  if (!panel || !stepsEl) return;

  // 计算完成步骤数
  const doneCount = (_ps.steps || []).filter((s) => s.status === "done").length;
  const total     = (_ps.steps || []).length;

  // 折叠步骤列表
  stepsEl.style.maxHeight = stepsEl.scrollHeight + "px";
  stepsEl.style.overflow  = "hidden";
  requestAnimationFrame(() => {
    stepsEl.style.transition = "max-height 0.4s ease";
    stepsEl.style.maxHeight  = "0px";
  });

  // 更新 head 变成可点击的折叠摘要
  if (headEl) {
    headEl.style.cursor = "pointer";
    headEl.title = "点击展开/折叠思考过程";
    const statusEl = qs("#thinking-status", headEl);
    if (statusEl) statusEl.textContent = `共 ${doneCount}/${total} 步 · 点击展开`;

    const arrow = document.createElement("span");
    arrow.id = "thinking-arrow";
    arrow.textContent = "▾";
    arrow.style.cssText = "margin-left:6px;font-size:12px;color:var(--ink-3);transition:transform 0.3s";
    headEl.appendChild(arrow);

    let expanded = false;
    headEl.addEventListener("click", () => {
      expanded = !expanded;
      if (expanded) {
        stepsEl.style.maxHeight = stepsEl.scrollHeight + 400 + "px";
        arrow.style.transform = "rotate(180deg)";
        if (statusEl) statusEl.textContent = `共 ${doneCount}/${total} 步 · 点击折叠`;
      } else {
        stepsEl.style.maxHeight = "0px";
        arrow.style.transform = "";
        if (statusEl) statusEl.textContent = `共 ${doneCount}/${total} 步 · 点击展开`;
      }
    });
  }
}

// ─── 路线结果渲染 ─────────────────────────────────────────────────────────────
function _renderPlan(root, plan) {
  const rc = qs("#result-container", root);
  if (!rc) return;

  const steps    = Array.isArray(plan.steps)    ? plan.steps    : [];
  const thinking = Array.isArray(plan.thinking)  ? plan.thinking  : [];
  const risks    = Array.isArray(plan.risks)     ? plan.risks     : [];
  const actions  = Array.isArray(plan.actions)   ? plan.actions   : [];
  const actionBundle = (plan.action_bundle && typeof plan.action_bundle === "object") ? plan.action_bundle : null;
  const scoreSummary = (plan.score_summary && typeof plan.score_summary === "object") ? plan.score_summary : {};
  const candidatePlans = Array.isArray(plan.candidate_plans) ? plan.candidate_plans : [];
  const profile  = (typeof plan.profile === "object" && plan.profile) ? plan.profile : {};
  const people   = profile.people || {};

  // 同行人信息
  const peopleDesc = people.description || "";
  const adultsCnt  = people.adults  || 0;
  const childCnt   = people.children || 0;
  const childAge   = people.child_age || "";
  let peopleInfo   = "";
  if (peopleDesc) peopleInfo = peopleDesc;
  else if (adultsCnt) {
    peopleInfo = `${adultsCnt}大人`;
    if (childCnt) peopleInfo += `+${childCnt}孩子${childAge ? `(${childAge})` : ""}`;
  }

  // 路线步骤（含独立操作按钮）
  const _todayStr = new Date().toISOString().split("T")[0]; // "2026-06-01"
  const _tomorrowStr = new Date(Date.now() + 86400000).toISOString().split("T")[0];
  const stepsHtml = steps.map((step, idx) => {
    // 双重判断：step.booked（plan 对象内）+ _bookedPlaceNames（sessionStorage 备份）
    const isBooked = step.booked === true || _bookedPlaceNames.has(step.name || "");
    const bookBtnHtml = isBooked
      ? `<button class="btn btn--ghost btn--sm" style="color:var(--green)" disabled>✓ 已加入行程</button>`
      : `<button class="btn btn--outline btn--sm step-book-btn"
            data-idx="${idx}" data-name="${escapeHtml(step.name || "")}"
            data-time="${escapeHtml(step.time || "")}"
            data-date="${escapeHtml(step.date || "")}"
            data-price="${escapeHtml(step.price_range || step.meta || "")}">
            预约此地点
          </button>`;
    // 时间显示：非今天则加日期前缀
    let timeDisplay = step.time || "";
    if (step.date && step.date !== _todayStr) {
      const dayLabel = step.date === _tomorrowStr ? "明天" : step.date.slice(5);
      timeDisplay = `${dayLabel} ${timeDisplay}`;
    }
    const phone = _extractPhone(step);
    const detailParts = [
      step.address,
      step.open_hours ? `营业 ${step.open_hours}` : "",
      step.price_range,
    ].filter(Boolean);
    // 占位符 SVG（加载真实地图图片前显示）
    const _CAT_ICON = {
      "景点": Icon.landmark, "餐厅": Icon.utensils, "咖啡": Icon.coffee, "酒吧": Icon.wine,
    };
    const mediaIcon = _CAT_ICON[step.category || ""] || Icon.mapPin;
    return `
    <div class="route-step" id="route-step-${idx}" data-step-idx="${idx}">
      <div class="route-step__time">${escapeHtml(timeDisplay)}</div>
      <div class="route-step__connector">
        <div class="route-step__dot">${idx + 1}</div>
        ${idx < steps.length - 1 ? '<div class="route-step__line"></div>' : ""}
      </div>
      <div class="route-step__body">
        <div class="route-step__name">${escapeHtml(step.name || "")}</div>
        ${step.meta   ? `<div class="route-step__meta">${escapeHtml(step.meta)}</div>` : ""}
        ${detailParts.length ? `<div class="route-step__meta route-step__meta--detail">${escapeHtml(detailParts.join(" · "))}</div>` : ""}
        ${step.reason ? `<div class="route-step__reason">${escapeHtml(step.reason)}</div>` : ""}
        <div class="step-actions">
          ${bookBtnHtml}
          <button class="btn btn--ghost btn--sm step-replace-btn"
            data-idx="${idx}" data-category="${escapeHtml(step.category || "")}"
            data-keyword="${escapeHtml(step.keyword || "")}">
            ↻ 换一个
          </button>
          ${phone ? `<a class="btn btn--ghost btn--sm step-call-btn" href="${escapeHtml(_phoneHref(phone))}">${Icon.phone} 电话</a>` : ""}
        </div>
      </div>
      <div class="route-step__media route-step__media--${escapeHtml(step.category || "地点")}"
        data-category="${escapeHtml(step.category || "")}">
        <div style="display:flex;flex-direction:column;align-items:center;gap:5px;color:var(--brand);opacity:0.7">
          <span style="width:28px;height:28px">${mediaIcon}</span>
          <span style="font-size:10px;color:var(--ink-3)">${escapeHtml(step.category || "地点")}</span>
        </div>
      </div>
    </div>`;
  }).join("");

  // 思考标签（规划依据）
  const thinkingHtml = thinking.length ? `
    <details class="thinking-detail" style="margin-top:12px">
      <summary class="thinking-detail__summary">查看规划依据 ▾</summary>
      <div class="tag-row" style="margin-top:8px">
        ${thinking.slice(0, 6).map((t) =>
          `<span class="tag tag--brand">${escapeHtml(String(t).slice(0, 80))}</span>`
        ).join("")}
      </div>
    </details>` : "";
  const thinkingSteps = Array.isArray(_ps.steps) ? _ps.steps : [];
  const doneThinkingCount = thinkingSteps.filter((s) => s.status === "done").length;
  const thinkingPanelHtml = thinkingSteps.length ? `
    <details class="route-thinking-detail">
      <summary>
        <span>AI 思考过程</span>
        <strong>${doneThinkingCount}/${thinkingSteps.length} 步</strong>
      </summary>
      <div class="thinking-steps thinking-steps--embedded">
        ${thinkingSteps.map((s) => _renderStep({
          ...s,
          obs: s.obs || (s.status === "done" ? `${s.label}完成` : "本次未返回该步骤结果"),
        })).join("")}
      </div>
    </details>` : "";
  const scoreHtml = (scoreSummary.candidate_count || candidatePlans.length) ? `
    <div class="planner-result-card score-summary-card" style="margin-bottom:16px">
      <div class="section-title">
        <h3>多方案评分</h3>
        <span class="tag tag--green">${escapeHtml(String(scoreSummary.candidate_count || candidatePlans.length))} 个候选</span>
      </div>
      <p class="action-bundle-card__summary">${escapeHtml(scoreSummary.selection_reason || "已按时间、距离、餐饮数量、预约可行性和同行人偏好比较候选方案。")}</p>
      <div class="tag-row" style="margin-top:8px">
        ${scoreSummary.selected_score ? `<span class="tag tag--brand">选中评分 ${escapeHtml(String(scoreSummary.selected_score))}</span>` : ""}
        ${scoreSummary.selected_feasibility ? `<span class="tag tag--green">预约可行</span>` : ""}
        ${candidatePlans.slice(0, 3).map((p, idx) => `<span class="tag">方案${idx + 1} ${escapeHtml(String(p.score || "--"))}分</span>`).join("")}
      </div>
    </div>` : "";

  // 风险提示
  const risksHtml = risks.length ? `
    <div class="tag-row" style="margin-top:10px">
      ${risks.slice(0, 3).map((r) => `<span class="tag tag--clay">${escapeHtml(r)}</span>`).join("")}
    </div>` : "";

  // 预约区（有 pending actions 时显示）
  const pendingActions = actions.filter((a) => a?.status === "pending");
  const reserveHtml = pendingActions.length ? `
    <div class="reserve-section" id="reserve-section">
      <div class="section-label" style="margin-bottom:8px">确认预约地点</div>
      ${pendingActions.map((a, i) => `
        <label class="reserve-item">
          <input type="checkbox" class="reserve-check" data-idx="${i}" checked />
          <span class="reserve-item__name">${escapeHtml(a.target || "")}</span>
          <span class="reserve-item__meta">${escapeHtml(a.time || "")} · ${a.people_count || 1}人</span>
        </label>`).join("")}
      <button id="do-reserve" class="btn btn--primary btn--wide" style="margin-top:10px">确认预约</button>
    </div>` : "";

  // 快捷预约（无 pending actions，但有 steps 可以全部预约）
  const quickReserveHtml = !pendingActions.length && steps.length ? `
    <button id="do-quick-reserve" class="btn btn--outline btn--wide" style="margin-top:12px">
      + 加入行程（全部预约）
    </button>` : "";
  const bundleItems = Array.isArray(actionBundle?.items) ? actionBundle.items : [];
  const actionBundleHtml = bundleItems.length ? `
    <div class="planner-result-card action-bundle-card" id="action-bundle-card" style="margin-bottom:16px">
      <div class="section-title">
        <h3>待确认动作</h3>
        <span class="tag tag--green">${bundleItems.length} 项</span>
      </div>
      <p class="action-bundle-card__summary">${escapeHtml(actionBundle.summary || "勾选要执行的动作，点击一键执行。")}</p>
      <div class="action-bundle-list">
        ${bundleItems.map((item, i) => {
          const alreadyBooked = _actionItemAlreadyBooked(item);
          const isSms = item?.type === "sms";
          const isShareRoute = item?.type === "share_route";
          const isCalendar = item?.type === "save_calendar";
          const isDisabled = alreadyBooked && !isShareRoute && !isCalendar;
          const disabledNote = alreadyBooked ? "该地点已在行程中，已避免重复预约。" : "";
          const bodyNote = isSms ? "需填写 +86 手机号后单独校验发送。"
            : disabledNote || (item.message || item.time || "确认后执行");
          return `
          <label class="action-bundle-item ${isDisabled ? "is-disabled" : ""}" style="cursor:pointer">
            <input type="checkbox" class="action-bundle-check" data-idx="${i}" ${isDisabled ? "disabled" : "checked"} />
            <span class="action-bundle-item__icon" style="color:var(--brand);flex-shrink:0">${_actionIcon(item.type)}</span>
            <span class="action-bundle-item__body">
              <strong>${escapeHtml(item.title || item.target || item.type || "待执行动作")}</strong>
              <small>${escapeHtml(bodyNote)}</small>
              ${isSms ? `
                <span class="sms-action-row">
                  <input class="sms-action-input" data-sms-idx="${i}" inputmode="tel" placeholder="+8613800138000" aria-label="短信接收手机号" />
                  <button type="button" class="btn btn--outline btn--sm sms-send-btn" data-sms-idx="${i}">发送校验</button>
                </span>
                <span class="sms-action-result" data-sms-result="${i}"></span>
              ` : ""}
            </span>
            <span class="action-bundle-item__type">${escapeHtml(_actionTypeLabel(item.type))}</span>
          </label>`;
        }).join("")}
      </div>
      <button id="execute-action-bundle" class="btn btn--primary btn--wide" style="margin-top:12px">
        一键执行已选动作
      </button>
      <div id="action-bundle-result" class="action-bundle-result"></div>
    </div>` : "";

  rc.innerHTML = `
    <!-- 当前路线卡片 -->
    <div class="plan-intro-card route-current-card" style="margin-bottom:16px">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
        <span style="font-size:13px;font-weight:600;color:var(--brand);letter-spacing:0.02em">当前路线</span>
        ${peopleInfo ? `<span style="font-size:12px;color:var(--ink-2);margin-left:auto">${escapeHtml(peopleInfo)}</span>` : ""}
        <button class="route-card-close" id="route-card-close" type="button" title="隐藏当前路线" style="color:var(--ink-3);margin-left:${peopleInfo?'0':'auto'}">×</button>
      </div>
      <p style="color:var(--ink);font-size:14px;line-height:1.65">${escapeHtml(plan.intro || "")}</p>
      ${thinkingHtml}
      ${risksHtml}
      ${thinkingPanelHtml}
    </div>

    <!-- 地图 -->
    <div class="planner-map-card" id="map-card" style="margin-bottom:16px">
      <div class="section-title" style="margin-bottom:10px"><h3>路线地图</h3></div>
      <div id="route-map" class="leaflet-route-map"></div>
      <p class="map-hint" id="route-map-hint">地点来自真实数据库 · 点击标记查看详情</p>
    </div>

	    <!-- 行程步骤（每步有独立操作）-->
	    <div class="planner-result-card" style="margin-bottom:16px">
      <div class="section-title">
        <h3>行程安排</h3>
        <span class="tag tag--green">${steps.length} 个地点</span>
      </div>
      <div class="route-timeline" id="route-timeline">
        ${stepsHtml || `<p style="color:var(--ink-3);font-size:13px">暂无步骤数据</p>`}
      </div>
      ${reserveHtml}
      ${quickReserveHtml}
	    </div>
	
	    ${scoreHtml}
	    ${actionBundleHtml}
	  `;

  _initMap(steps);
  _bindStepActions(rc, plan);
  _bindPlanLevelActions(rc, plan);
  _hydrateStepImages(rc, steps);  // 传入 steps 以获取 lng/lat
  qs("#route-card-close", rc)?.addEventListener("click", () => {
    _ps.routeHidden = true;
    const conv = _activeConversation();
    conv.lastPlan = _ps.finalPlan || conv.lastPlan;
    conv.routeHidden = true;
    conv.updatedAt = Date.now();
    _saveConversations();
    rc.innerHTML = "";
  });
}

function _renderTextReply(root, text) {
  const rc = qs("#result-container", root);
  if (!rc) return;
  rc.innerHTML = `
    <div class="card" style="padding:16px 20px">
      <div style="display:flex;gap:8px;align-items:flex-start">
        <span style="width:20px;height:20px;flex-shrink:0;color:var(--brand)">${Icon.sparkle}</span>
        <p style="line-height:1.8;color:var(--ink);margin:0">${escapeHtml(text)}</p>
      </div>
    </div>`;
}

function _actionTypeLabel(type) {
  const labels = {
    reserve: "预约",
    order_gift: "下单",
    notify: "通知",
    share: "转发",
    sms: "短信",
    share_route: "分享",
    save_calendar: "日历",
  };
  return labels[type] || "动作";
}

function _actionIcon(type) {
  const icons = {
    reserve:       `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" width="16" height="16"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>`,
    order_gift:    `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" width="16" height="16"><polyline points="20 12 20 22 4 22 4 12"/><rect x="2" y="7" width="20" height="5"/><line x1="12" y1="22" x2="12" y2="7"/><path d="M12 7H7.5a2.5 2.5 0 010-5C11 2 12 7 12 7z"/><path d="M12 7h4.5a2.5 2.5 0 000-5C13 2 12 7 12 7z"/></svg>`,
    notify:        `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" width="16" height="16"><path d="M18 8A6 6 0 006 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 01-3.46 0"/></svg>`,
    share:         `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" width="16" height="16"><path d="M22 16.9v3a2 2 0 01-2.2 2 19.8 19.8 0 01-8.6-3.1 19.5 19.5 0 01-5.3-5.3 19.8 19.8 0 01-3.1-8.7A2 2 0 014.7 3h3a2 2 0 012 1.7 12.8 12.8 0 00.7 2.8 2 2 0 01-.5 2.1L9.1 10.4a16 16 0 006.1 6.1l.7-.8a2 2 0 012.1-.4 12.8 12.8 0 002.8.7A2 2 0 0122 17z"/></svg>`,
    sms:           `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" width="16" height="16"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg>`,
    share_route:   `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" width="16" height="16"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="3" height="3"/><rect x="19" y="14" width="2" height="2"/><rect x="14" y="19" width="2" height="2"/><rect x="18" y="19" width="3" height="2"/></svg>`,
    save_calendar: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" width="16" height="16"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18M9 16l2 2 4-4"/></svg>`,
  };
  return icons[type] || `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" width="16" height="16"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><circle cx="12" cy="16" r="1" fill="currentColor" stroke="none"/></svg>`;
}

function _actionReserveName(item) {
  if (!item || item.type !== "reserve") return "";
  return String(item?.payload?.place_name || item.target || "").trim();
}

function _actionItemAlreadyBooked(item) {
  const name = _actionReserveName(item);
  return !!name && _bookedPlaceNames.has(name);
}

function _showShareQRModal(shareUrl, title) {
  const existing = document.getElementById("share-qr-modal");
  if (existing) existing.remove();
  const modal = document.createElement("div");
  modal.id = "share-qr-modal";
  modal.className = "official-modal";
  modal.innerHTML = `
    <div class="official-modal__dialog" style="max-width:320px">
      <div class="official-modal__head">
        <strong>分享行程</strong>
      </div>
      <div style="padding:16px 0;text-align:center">
        <div id="share-qr-box" style="display:inline-block;background:#fff;padding:10px;border-radius:10px;box-shadow:0 1px 8px rgba(0,0,0,0.1)"></div>
        <p style="margin:12px 0 4px;font-size:13px;font-weight:500;color:var(--ink)">${escapeHtml(title || "扫码查看行程")}</p>
        <p style="margin:0;font-size:11px;color:var(--ink-3);word-break:break-all;max-width:260px;margin:0 auto">${escapeHtml(shareUrl)}</p>
      </div>
      <div class="official-modal__actions">
        <button type="button" class="btn btn--ghost btn--sm" data-modal-cancel>关闭</button>
        <button type="button" class="btn btn--outline btn--sm" id="copy-share-link">复制链接</button>
      </div>
    </div>`;
  document.body.appendChild(modal);
  try {
    new QRCode(document.getElementById("share-qr-box"), {
      text: shareUrl, width: 160, height: 160,
      colorDark: "#1C1C1E", colorLight: "#ffffff",
      correctLevel: QRCode?.CorrectLevel?.M,
    });
  } catch (e) {
    document.getElementById("share-qr-box").innerHTML = `<p style="font-size:11px;color:#888;padding:8px">二维码生成失败，请复制链接</p>`;
  }
  modal.addEventListener("click", (e) => {
    if (e.target === modal || e.target.closest("[data-modal-cancel]")) modal.remove();
  });
  document.getElementById("copy-share-link")?.addEventListener("click", async (event) => {
    const btn = event.currentTarget;
    const oldText = btn.textContent;
    const ok = await _copyText(shareUrl);
    if (ok) {
      btn.textContent = "已复制";
      toast("链接已复制");
      setTimeout(() => { btn.textContent = oldText; }, 1200);
    } else {
      toast(`复制失败，请手动复制：${shareUrl}`);
    }
  });
}

async function _copyText(text) {
  const value = String(text || "");
  if (!value) return false;
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(value);
      return true;
    } catch {}
  }
  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  textarea.style.top = "0";
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  textarea.setSelectionRange(0, value.length);
  try {
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    textarea.remove();
  }
}

function _downloadCalendar(payload, plan) {
  const title = encodeURIComponent(payload.title || "半日出行");
  const steps = Array.isArray(payload.steps) ? payload.steps : [];
  const stepsText = steps.map((s) => `${s.time || ""} ${s.name || ""}`).filter(Boolean).join("\\n");
  const dateStr = (payload.date || "").replace(/-/g, "") || "";
  const startTime = (payload.start_time || "14:00").replace(":", "");
  const dtStart = dateStr ? `${dateStr}T${startTime}00` : "";
  const dtEnd = dateStr ? `${dateStr}T${(parseInt(startTime, 10) + 200).toString().padStart(4, "0")}00` : "";
  const ics = [
    "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//半日闲AI//CN",
    "BEGIN:VEVENT",
    `SUMMARY:${payload.title || "半日出行"}`,
    dtStart ? `DTSTART:${dtStart}` : "",
    dtEnd ? `DTEND:${dtEnd}` : "",
    stepsText ? `DESCRIPTION:${stepsText}` : "",
    "END:VEVENT", "END:VCALENDAR",
  ].filter(Boolean).join("\r\n");
  const blob = new Blob([ics], { type: "text/calendar;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = "半日出行提醒.ics"; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 3000);
}

function _normalizeSmsPhone(phone) {
  let raw = String(phone || "").trim().replace(/[\s\-()（）]/g, "");
  if (raw.startsWith("0086")) raw = `+86${raw.slice(4)}`;
  if (raw.startsWith("86") && !raw.startsWith("+86")) raw = `+${raw}`;
  if (!raw.startsWith("+86") && /^1[3-9]\d{9}$/.test(raw)) raw = `+86${raw}`;
  return raw;
}

function _isValidCnMobile(phone) {
  return /^\+861[3-9]\d{9}$/.test(_normalizeSmsPhone(phone));
}

function _showOfficialModal({ title, body, confirmText = "确认继续", cancelText = "取消" }) {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "official-modal";
    overlay.innerHTML = `
      <div class="official-modal__dialog" role="dialog" aria-modal="true" aria-labelledby="official-modal-title">
        <div class="official-modal__head">
          <strong id="official-modal-title">${escapeHtml(title)}</strong>
        </div>
        <div class="official-modal__body">${body.map((line) => `<p>${escapeHtml(line)}</p>`).join("")}</div>
        <div class="official-modal__actions">
          <button type="button" class="btn btn--ghost btn--sm" data-modal-cancel>${escapeHtml(cancelText)}</button>
          <button type="button" class="btn btn--primary btn--sm" data-modal-confirm>${escapeHtml(confirmText)}</button>
        </div>
      </div>`;
    const cleanup = (value) => {
      overlay.remove();
      resolve(value);
    };
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay || event.target.closest("[data-modal-cancel]")) cleanup(false);
      if (event.target.closest("[data-modal-confirm]")) cleanup(true);
    });
    document.body.appendChild(overlay);
    overlay.querySelector("[data-modal-confirm]")?.focus();
  });
}

async function _showSmsRestrictedNotice(phone, item = {}) {
  const normalized = _normalizeSmsPhone(phone);
  return _showOfficialModal({
    title: "短信发送受限提示",
    body: [
      `手机号 ${normalized} 已通过格式校验。`,
      "根据国内短信服务商实名制管理、短信签名与模板审核要求，面向 +86 手机号的真实短信发送通常需要完成企业认证或具备合规主体资质。",
      "当前演示环境未配置企业短信资质和已审核短信模板，因此不会真实发送短信；系统仅保留待发送文案和模拟回执，避免误触达用户手机号。",
      `待发送用途：${item?.target || "行程确认"}；生产环境接入已认证短信服务后可启用真实发送。`,
    ],
    confirmText: "我知道了",
  });
}

function _planPeopleCount(plan) {
  const people = plan?.profile?.people || {};
  const total = Number(people.adults || 0) + Number(people.children || 0);
  return total > 0 ? total : 1;
}

// ─── 步骤操作：单独预约 + 换一个 ─────────────────────────────────────────────
function _bindStepActions(rc, plan) {
  const timeline = qs("#route-timeline", rc);
  if (!timeline) return;

  timeline.addEventListener("click", async (e) => {
    // 单步预约
    const bookBtn = e.target.closest(".step-book-btn");
    if (bookBtn) {
      await _bookStep(bookBtn, plan);
      return;
    }
    // 换一个地点
    const replaceBtn = e.target.closest(".step-replace-btn");
    if (replaceBtn) {
      await _replaceStep(replaceBtn, rc, plan);
    }
  });
}

function _bindPlanLevelActions(rc, plan) {
  qs("#do-reserve", rc)?.addEventListener("click", async (event) => {
    const btn = event.currentTarget;
    const checks = qsa(".reserve-check:checked", rc);
    const pendingActions = (plan.actions || []).filter((a) => a?.status === "pending");
    const reservations = checks.map((check) => {
      const action = pendingActions[parseInt(check.dataset.idx, 10)] || {};
      return {
        place_name: action.target || "",
        time: action.time || "",
        people_count: action.people_count || _planPeopleCount(plan),
        price: action.price || "",
        city: plan?.profile?.city || _currentCity(),
      };
    }).filter((item) => item.place_name);
    if (!reservations.length) { toast("请选择要预约的地点"); return; }
    btn.disabled = true; btn.textContent = "预约中…";
    try {
      await orderApi.confirmReservations({
        plan_summary: _ps.userText || plan.intro || "AI 规划路线",
        city: plan?.profile?.city || _currentCity(),
        reservations,
      });
      reservations.forEach((item) => _bookedPlaceNames.add(item.place_name));
      _persistBooked();
      toast("已加入行程");
      _renderPlan(document, plan);
    } catch (err) {
      toast(err.message || "预约失败");
      btn.disabled = false; btn.textContent = "确认预约";
    }
  });

  qs("#do-quick-reserve", rc)?.addEventListener("click", async (event) => {
    const btn = event.currentTarget;
    const steps = Array.isArray(plan.steps) ? plan.steps : [];
    const reservations = steps
      .filter((step) => step?.name && !_bookedPlaceNames.has(step.name))
      .map((step) => ({
        place_name: step.name,
        time: step.time || "",
        date: step.date || "",
        people_count: _planPeopleCount(plan),
        price: step.price_range || "",
        city: plan?.profile?.city || _currentCity(),
      }));
    if (!reservations.length) { toast("当前路线已加入行程"); return; }
    btn.disabled = true; btn.textContent = "加入中…";
    try {
      await orderApi.confirmReservations({
        plan_summary: _ps.userText || plan.intro || "AI 规划路线",
        city: plan?.profile?.city || _currentCity(),
        reservations,
      });
      reservations.forEach((item) => _bookedPlaceNames.add(item.place_name));
      _persistBooked();
      toast("已全部加入行程");
      _renderPlan(document, plan);
    } catch (err) {
      toast(err.message || "加入失败");
      btn.disabled = false; btn.textContent = "+ 加入行程（全部预约）";
    }
  });

  qs("#execute-action-bundle", rc)?.addEventListener("click", async (event) => {
    const btn = event.currentTarget;
    const bundle = plan.action_bundle || {};
    const allItems = Array.isArray(bundle.items) ? bundle.items : [];
    const checkedIndexes = qsa(".action-bundle-check:checked", rc)
      .map((check) => parseInt(check.dataset.idx, 10))
      .filter((idx) => Number.isFinite(idx));
    const selectedItems = checkedIndexes.map((idx) => allItems[idx]).filter(Boolean);

    // 分离客户端动作（share_route / save_calendar）和服务端动作
    const clientItems = selectedItems.filter((item) => item?.type === "share_route" || item?.type === "save_calendar");
    const executableItems = selectedItems.filter((item) => item?.type !== "sms" && item?.type !== "share_route" && item?.type !== "save_calendar" && !_actionItemAlreadyBooked(item));

    if (!clientItems.length && !executableItems.length) { toast("所选预约已在行程中，无需重复执行"); return; }
    btn.disabled = true; btn.textContent = "执行中…";

    const resultEl = qs("#action-bundle-result", rc);
    const receipts = [];

    // 处理客户端动作
    for (const item of clientItems) {
      if (item.type === "share_route") {
        try {
          const res = await shareApi.createShare({ plan, title: _ps.userText || plan.intro || "半日出行" });
          const shareUrl = res?.data?.share_url || "";
          if (shareUrl) {
            _showShareQRModal(shareUrl, res.data?.title || "行程分享");
            receipts.push(`✓ 行程分享链接已生成`);
          }
        } catch (err) {
          receipts.push(`⚠ 分享生成失败：${err.message || "请重试"}`);
        }
      } else if (item.type === "save_calendar") {
        // 生成 .ics 数据并触发下载
        try {
          _downloadCalendar(item.payload || {}, plan);
          receipts.push(`✓ 日历文件已下载，请在手机导入提醒`);
        } catch {
          receipts.push(`✓ 日历提醒已准备（请手动添加到日历）`);
        }
      }
    }

    // 处理服务端动作
    if (executableItems.length) {
      try {
        const res = await orderApi.executeActions({
          plan_summary: _ps.userText || plan.intro || "AI 规划路线",
          city: plan?.profile?.city || _currentCity(),
          action_bundle: { ...bundle, items: executableItems },
        });
        const executed = res?.data?.executed || [];
        executed
          .filter((item) => item.type === "reserve" && item.target)
          .forEach((item) => _bookedPlaceNames.add(item.target));
        _persistBooked();
        executed.forEach((item) => receipts.push(`✓ ${item.message || `${item.target || ""} 已执行`}`));
      } catch (err) {
        toast(err.message || "动作执行失败");
        btn.disabled = false; btn.textContent = "一键执行已选动作";
        return;
      }
    }

    if (resultEl && receipts.length) {
      resultEl.innerHTML = receipts.map((r) => `<div class="action-bundle-receipt">${escapeHtml(r)}</div>`).join("");
    }
    btn.textContent = "✓ 已执行";
    if (!clientItems.some((i) => i.type === "share_route")) toast("动作已执行");
  });

  qsa(".sms-send-btn", rc).forEach((smsBtn) => {
    smsBtn.addEventListener("click", async (event) => {
      const btn = event.currentTarget;
      const idx = parseInt(btn.dataset.smsIdx, 10);
      const item = (plan.action_bundle?.items || [])[idx] || {};
      const input = qs(`.sms-action-input[data-sms-idx="${idx}"]`, rc);
      const resultEl = qs(`[data-sms-result="${idx}"]`, rc);
      const phone = _normalizeSmsPhone(input?.value || "");
      if (!_isValidCnMobile(phone)) {
        toast("请输入正确的 +86 手机号");
        input?.focus();
        if (resultEl) resultEl.textContent = "手机号格式应为 +8613xxxxxxxxx";
        return;
      }
      await _showSmsRestrictedNotice(phone, item);
      if (resultEl) resultEl.textContent = "手机号已校验；当前环境受企业资质限制，短信未真实发送。";
      toast("短信未真实发送，已生成受限提示");
    });
  });
}

// 单个图片元素加载（AMap 优先，加载成功后后台缓存到服务端）
function _hydrateOneImage(el, name, sourceId = "") {
  if (!el || !name || typeof AMap === "undefined") return;
  // 使用当前城市搜索，避免西安等城市用北京范围找不到
  const city = (typeof state !== "undefined" && state.user?.city) || "北京";
  AMap.plugin("AMap.PlaceSearch", () => {
    const search = new AMap.PlaceSearch({ city, extensions: "all", pageSize: 1 });
    search.search(name, (status, result) => {
      if (status !== "complete") return;
      const pois = result?.poiList?.pois || [];
      if (!pois.length) return;
      const poi = pois[0];
      const photos = poi?.photos || [];
      if (!photos.length) return;
      const photoUrl = (photos[0]?.url || "").trim();
      if (!photoUrl) return;
      _loadImg(el, photoUrl, name);
      // 后台静默缓存到服务端（不影响前端显示）
      const cacheId = sourceId || poi.id || "";
      if (cacheId && state.token) {
        fetch(`/api/v1/map/place-image?source_id=${encodeURIComponent(cacheId)}&source=amap`, {
          headers: { Authorization: `Bearer ${state.token}` },
        }).catch(() => {});
      }
    });
  });
}

function _loadImg(el, src, alt) {
  const img = new Image();
  img.onload = () => {
    el.innerHTML = "";
    el.classList.add("has-image");
    el.appendChild(img);
  };
  img.onerror = () => {};
  img.alt = alt;
  img.src = src;
}

function _hydrateStepImages(rc, steps) {
  if (typeof AMap === "undefined" || !steps || !steps.length) return;
  const mediaEls = qsa(".route-step__media", rc);
  mediaEls.forEach((el, i) => {
    const step = steps[i];
    if (!step || !step.name) return;
    _hydrateOneImage(el, step.name, step.source_id || "");
  });
}

async function _bookStep(btn, plan) {
  const idx      = parseInt(btn.dataset.idx, 10);
  const name     = btn.dataset.name;
  const time     = btn.dataset.time;
  const date     = btn.dataset.date || "";   // 预约日期（空则后端用今天）
  const price    = btn.dataset.price;
  const people   = _planPeopleCount(plan);

  if (!name) { toast("地点信息缺失"); return; }
  // 硬拦截：地点名已在 sessionStorage 记录，绝对不重复调 API
  if (_bookedPlaceNames.has(name)) {
    btn.textContent = "✓ 已加入行程";
    btn.classList.remove("btn--outline");
    btn.classList.add("btn--ghost");
    btn.style.color = "var(--green)";
    btn.disabled = true;
    toast(`${name} 已在行程中`);
    return;
  }
  btn.disabled = true; btn.textContent = "预约中…";
  try {
    await orderApi.confirmReservations({
      plan_summary: _ps.userText || plan.intro || "AI 规划路线",
      city: plan?.profile?.city || _currentCity(),
      reservations: [{ place_name: name, time, date, people_count: people, price, city: plan?.profile?.city || _currentCity() }],
    });
    // 三重持久化：plan 对象 + sessionStorage + localStorage
    if (Array.isArray(plan.steps) && plan.steps[idx]) {
      plan.steps[idx].booked = true;
    }
    _bookedPlaceNames.add(name);   // sessionStorage 备份（tab 切换/刷新都生效）
    _persistBooked();
    setLastPlan(_ps.finalPlan);    // localStorage（含 booked 标记）
    _ps.bookedSteps.add(idx);
    toast(`${name} 已加入行程`);
    _renderPlan(document, plan);
  } catch (err) {
    toast(err.message || "预约失败");
    btn.disabled = false; btn.textContent = "预约此地点";
  }
}

function _syncActionBundleAfterReplace(plan, idx, newStep, oldName) {
  const bundle = plan?.action_bundle;
  const items = Array.isArray(bundle?.items) ? bundle.items : [];
  if (!items.length || !newStep?.name) return;
  const stepNames = new Set((plan.steps || []).map((step) => step?.name).filter(Boolean));
  const reserveItems = items.filter((item) => item?.type === "reserve");
  let changed = false;

  const updateReserveItem = (item) => {
    item.target = newStep.name;
    item.title = `预约 ${newStep.name}`;
    item.time = newStep.time || "";
    item.date = newStep.date || "";
    item.price = newStep.price_range || "";
    item.payload = {
      ...(item.payload || {}),
      place_name: newStep.name,
      time: newStep.time || "",
      date: newStep.date || "",
      price: newStep.price_range || "",
    };
    item.message = "已根据替换后的地点更新预约草稿。";
    changed = true;
  };

  for (const item of reserveItems) {
    const currentName = _actionReserveName(item);
    if (currentName === oldName || !stepNames.has(currentName)) {
      updateReserveItem(item);
      break;
    }
  }
  const giftItems = items.filter((item) => item?.type === "order_gift");
  for (const item of giftItems) {
    if (item.payload?.deliver_to === oldName) {
      item.payload = { ...(item.payload || {}), deliver_to: newStep.name };
      item.message = String(item.message || "").replace(oldName, newStep.name) || `已更新送达地点：${newStep.name}`;
      changed = true;
    }
  }
  if (changed) plan.action_bundle = { ...bundle, items };
}

async function _replaceStep(btn, rc, plan) {
  const idx      = parseInt(btn.dataset.idx, 10);
  const category = btn.dataset.category || "";
  const keyword  = btn.dataset.keyword  || "";

  btn.disabled = true; btn.innerHTML = `<span class="spinner-sm" style="display:inline-block"></span> 换中…`;

  try {
    const res = await chatApi.replaceStep({
      plan_steps:  plan.steps || [],
      step_index:  idx,
      category,
      keyword,
      city: plan?.profile?.city || _currentCity(),
    });
    const newStep = res?.data?.step;
    if (!newStep) { toast("暂无其他备选地点"); return; }

    // 更新 plan.steps 里这一步
    const newSteps = [...(plan.steps || [])];
    const oldName = newSteps[idx]?.name || "";
    newSteps[idx] = { ...newSteps[idx], ...newStep };
    plan.steps = newSteps;
    _syncActionBundleAfterReplace(plan, idx, newSteps[idx], oldName);
    if (_ps.finalPlan) _ps.finalPlan = { ..._ps.finalPlan, steps: newSteps, action_bundle: plan.action_bundle };

    // 局部更新这个步骤的 DOM
    const stepEl = qs(`#route-step-${idx}`, rc);
    if (stepEl) {
      const s = newSteps[idx];
      stepEl.querySelector(".route-step__name").textContent  = s.name || "";
      const metaEl   = stepEl.querySelector(".route-step__meta");
      const reasonEl = stepEl.querySelector(".route-step__reason");
      if (metaEl)   metaEl.textContent   = s.meta   || "";
      if (reasonEl) reasonEl.textContent = `💡 ${s.reason || ""}`;
      // 更新 data 属性
      const newBookBtn = stepEl.querySelector(".step-book-btn");
      if (newBookBtn) {
        newBookBtn.dataset.name  = s.name || "";
        newBookBtn.dataset.price = s.price_range || "";
        newBookBtn.textContent   = "预约此地点";
        newBookBtn.disabled      = false;
      }
      // 重置图片区域为 SVG 占位符，然后重新加载新地点图片
      const mediaEl = stepEl.querySelector(".route-step__media");
      if (mediaEl) {
        const _catIcon2 = { "景点": Icon.landmark, "餐厅": Icon.utensils, "咖啡": Icon.coffee, "酒吧": Icon.wine };
        const catIcon2 = _catIcon2[s.category || ""] || Icon.mapPin;
        mediaEl.classList.remove("has-image");
        mediaEl.innerHTML = `<div style="display:flex;flex-direction:column;align-items:center;gap:5px;color:var(--brand);opacity:0.7">
          <span style="width:28px;height:28px">${catIcon2}</span>
          <span style="font-size:10px;color:var(--ink-3)">${escapeHtml(s.category || "地点")}</span>
        </div>`;
        _hydrateOneImage(mediaEl, s.name, s.source_id || "");
      }
    }

    toast(`已替换为：${newStep.name}`);
    _renderPlan(document, plan);
  } catch (err) {
    toast(err.message || "替换失败");
  } finally {
    btn.disabled = false; btn.textContent = "↻ 换一个";
  }
}

// ─── 高德地图 ──────────────────────────────────────────────────────────────────
function _setMapHint(text, tone = "") {
  const hint = qs("#route-map-hint");
  if (!hint) return;
  hint.textContent = text;
  hint.classList.toggle("map-hint--warn", tone === "warn");
}

function _toLngLat(step) {
  const lng = parseFloat(step?.lng);
  const lat = parseFloat(step?.lat);
  if (!Number.isFinite(lng) || !Number.isFinite(lat) || lng === 0 || lat === 0) return null;
  return new AMap.LngLat(lng, lat);
}

function _extractRoutePath(result) {
  const path = [];
  const route = result?.routes?.[0];
  (route?.steps || []).forEach((step) => {
    if (Array.isArray(step.path)) path.push(...step.path);
  });
  if (!path.length && Array.isArray(route?.path)) path.push(...route.path);
  return path;
}

function _searchRouteSegment(toolName, origin, dest) {
  return new Promise((resolve) => {
    AMap.plugin(`AMap.${toolName}`, () => {
      const Tool = AMap[toolName];
      if (!Tool) {
        resolve({ ok: false, path: [], message: `${toolName} 插件不可用` });
        return;
      }
      const planner = new Tool({ policy: 0, extensions: "base", autoFitView: false });
      planner.search(origin, dest, (status, result) => {
        const path = status === "complete" ? _extractRoutePath(result) : [];
        resolve({
          ok: path.length > 0,
          path,
          message: result?.info || result?.message || status || `${toolName} 路线失败`,
        });
      });
    });
  });
}

async function _drawRoadRoute(points) {
  if (!_amapInstance || points.length < 2) return;

  const fullPath = [];
  const failures = [];

  for (let i = 0; i < points.length - 1; i++) {
    const driving = await _searchRouteSegment("Driving", points[i], points[i + 1]);
    let segment = driving;
    if (!segment.ok) {
      const walking = await _searchRouteSegment("Walking", points[i], points[i + 1]);
      segment = walking.ok ? walking : driving;
      if (!walking.ok) failures.push(`${i + 1}-${i + 2}: ${driving.message}`);
    }
    if (segment.ok) fullPath.push(...segment.path);
  }

  if (fullPath.length) {
    _amapInstance.add(new AMap.Polyline({
      path: fullPath,
      strokeColor: "#E8480A",
      strokeWeight: 5,
      strokeOpacity: 0.92,
      lineJoin: "round",
      lineCap: "round",
      zIndex: 50,
    }));
    _setMapHint(
      failures.length
        ? `已绘制可用道路路线，${failures.length} 段路线服务失败`
        : "已按真实道路绘制路线 · 点击标记查看详情",
      failures.length ? "warn" : "",
    );
  } else {
    _amapInstance.add(new AMap.Polyline({
      path: points,
      strokeColor: "#E8480A",
      strokeWeight: 4,
      strokeOpacity: 0.7,
      strokeStyle: "dashed",
      strokeDasharray: [12, 6],
      lineJoin: "round",
      zIndex: 50,
    }));
    _setMapHint(`高德路线服务未返回道路路径，已临时显示直线：${failures[0] || "请检查 Key / 安全密钥 / 域名白名单"}`, "warn");
  }

  _amapInstance.setFitView(null, false, [50, 50, 60, 50]);
}

function _initMap(steps) {
  const mapEl = qs("#route-map");
  if (!mapEl) return;

  if (_amapInstance) {
    try { _amapInstance.destroy(); } catch (_) {}
    _amapInstance = null;
  }

  if (typeof AMap === "undefined") {
    qs("#map-card")?.classList.add("is-hidden");
    return;
  }

  const valid = steps.filter((s) => _toLngLat(s));
  if (!valid.length) { qs("#map-card")?.classList.add("is-hidden"); return; }
  qs("#map-card")?.classList.remove("is-hidden");
  _setMapHint("地点来自真实数据库 · 正在加载高德地图路线");

  // 数据库里的北京 POI 坐标按高德坐标直接使用；错误地每次做 GPS 转换会把点偏移。
  const points = valid.map(_toLngLat);

  _amapInstance = new AMap.Map(mapEl, {
    zoom: 13,
    center: points[0],
    viewMode: "2D",
    resizeEnable: true,
    mapStyle: "amap://styles/normal",
    features: ["bg", "road", "building", "point"],
    layers: [new AMap.TileLayer()],
  });

  _amapInstance.on("complete", () => {
    try { _amapInstance.resize(); } catch (_) {}
  });

  AMap.plugin(["AMap.Scale", "AMap.ToolBar"], () => {
    try {
      _amapInstance.addControl(new AMap.Scale());
      _amapInstance.addControl(new AMap.ToolBar({ position: "RB" }));
    } catch (_) {}
  });

  // 自定义数字标记（品牌橙色）
  points.forEach((pos, i) => {
    const s = valid[i];
    const marker = new AMap.Marker({
      position: pos,
      content: `<div style="width:32px;height:32px;border-radius:50%;background:#E8480A;color:#fff;font-size:14px;font-weight:700;display:flex;align-items:center;justify-content:center;border:3px solid #fff;box-shadow:0 2px 10px rgba(232,72,10,0.5);cursor:pointer;position:relative;z-index:200">${i + 1}</div>`,
      offset: new AMap.Pixel(-16, -16),
      zIndex: 110 + i,
    });
    const timeStr = s.date && s.date !== new Date().toISOString().split("T")[0]
      ? `明天 ${s.time || ""}` : (s.time || "");
    const info = new AMap.InfoWindow({
      content: `<div style="padding:10px 14px;font-size:13px;max-width:200px;line-height:1.6">
        <strong style="display:block;font-size:14px;margin-bottom:4px">${escapeHtml(s.name || "")}</strong>
        ${timeStr ? `<span style="color:#E8480A;font-weight:600;font-size:12px">${escapeHtml(timeStr)}</span><br>` : ""}
        ${s.meta ? `<span style="color:#636366;font-size:12px">${escapeHtml(String(s.meta).slice(0, 60))}</span>` : ""}
      </div>`,
      offset: new AMap.Pixel(0, -32),
    });
    marker.on("click", () => info.open(_amapInstance, pos));
    _amapInstance.add(marker);
  });

  if (points.length >= 2) {
    _drawRoadRoute(points);
  } else {
    _amapInstance.setFitView(null, false, [50, 50, 60, 50]);
    _setMapHint("地点来自真实数据库 · 点击标记查看详情");
  }
}
