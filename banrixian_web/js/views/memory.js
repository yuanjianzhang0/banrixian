import { memoryApi } from "../api.js";
import { escapeHtml, qs, toast } from "../ui.js";
import { clearChatState, state } from "../state.js";
import { Icon } from "../icons.js";

export async function renderMemory(root) {
  root.innerHTML = `<div class="empty-state"><div class="spinner"></div><span>正在读取记忆…</span></div>`;
  try {
    const res  = await memoryApi.snapshot();
    const data = res?.data || {};
    root.innerHTML = _buildHTML(data);
    _bindEvents(root);
  } catch (err) {
    root.innerHTML = `<div class="empty-state"><strong>加载失败</strong><span>${escapeHtml(err.message)}</span></div>`;
  }
}

function _buildHTML(data) {
  // API 真实字段名
  const preferenceMemory = Array.isArray(data.preferenceMemory) ? data.preferenceMemory : [];
  const familyMemory     = Array.isArray(data.familyMemory)     ? data.familyMemory     : [];
  const sessionHistory   = Array.isArray(data.sessionHistory)   ? data.sessionHistory   : [];

  // 拆分偏好记忆：长期偏好 vs 亲友偏好
  const longTermPrefs  = preferenceMemory.filter((m) => m.memory_type === "preference");
  const familyPrefs    = preferenceMemory.filter((m) => m.memory_type === "family_preference");

  const backBtn = state.previousRoute === "profile"
    ? `<button class="page-back-btn" id="memory-back">← 返回个人中心</button>`
    : "";

  return `
    <div class="planner-wrap">
      ${backBtn}
      <div>
        <h2 class="planner-heading">AI 记忆</h2>
        <p class="planner-subtext">AI 从对话中学习到的偏好与亲友信息</p>
      </div>

      <!-- 长期偏好 -->
      <div class="panel">
        <div class="panel__head">
          <h3>长期偏好</h3>
          <span class="tag tag--clay">${longTermPrefs.length} 条</span>
        </div>
        ${longTermPrefs.length
          ? `<div class="memory-list">${longTermPrefs.map((m) => _prefCard(m, "clay")).join("")}</div>`
          : _empty("暂无长期偏好", "与 AI 对话后自动记录")
        }
      </div>

      <!-- 亲友偏好记忆 -->
      <div class="panel">
        <div class="panel__head">
          <h3>亲友偏好记忆</h3>
          <span class="tag tag--brand">${familyPrefs.length} 条</span>
        </div>
        ${familyPrefs.length
          ? `<div class="memory-list">${familyPrefs.map((m) => _prefCard(m, "brand")).join("")}</div>`
          : _empty("暂无亲友偏好记忆", "提及家人出行时自动记录")
        }
      </div>

      <!-- 亲友画像 -->
      <div class="panel">
        <div class="panel__head">
          <h3>亲友画像</h3>
          <span class="tag">${familyMemory.length} 位</span>
        </div>
        ${familyMemory.length
          ? `<div class="family-portrait-list">${familyMemory.map(_familyCard).join("")}</div>`
          : _empty("暂无亲友画像", "聊到家人后自动提取")
        }
      </div>

      <!-- 短期会话 -->
      <div class="panel">
        <div class="panel__head">
          <h3>⏱ 短期会话</h3>
          <div style="display:flex;align-items:center;gap:8px">
            <span class="tag">${sessionHistory.length} 条</span>
            <button id="clear-session-btn" class="btn btn--outline btn--sm">清空上下文</button>
          </div>
        </div>
        <div class="conv-list" id="conv-list">
          ${sessionHistory.length
            ? sessionHistory.slice(-30).map(_convItem).join("")
            : _empty("暂无短期会话", "当前会话对话记录会显示在这里")
          }
        </div>
      </div>
    </div>
  `;
}

// ── 卡片渲染 ──────────────────────────────────────────────────────────────────

function _prefCard(item, color) {
  const tagCls = color === "clay" ? "tag--clay" : color === "brand" ? "tag--brand" : "";
  const title  = item.title || "记忆";
  const content = item.content || "";
  const relation = item.relation ? `<span class="tag" style="margin-left:4px">${escapeHtml(item.relation)}</span>` : "";
  const weight = item.weight > 1 ? `<span class="memory-card__weight">×${item.weight}</span>` : "";

  return `
    <div class="memory-card">
      <div class="memory-card__header">
        <div class="memory-card__title">${escapeHtml(title)}${relation}</div>
        <div style="display:flex;align-items:center;gap:6px">
          ${weight}
          <span class="tag ${tagCls}">${escapeHtml(item.memory_type || "偏好")}</span>
        </div>
      </div>
      ${content ? `<div class="memory-card__content">${escapeHtml(content)}</div>` : ""}
      ${item.updated_at ? `<div class="memory-card__time">${escapeHtml(String(item.updated_at).slice(0, 16))}</div>` : ""}
    </div>`;
}

function _familyCard(member) {
  const tags = Array.isArray(member.tags) ? member.tags : [];
  return `
    <div class="family-portrait-card">
      <div class="family-portrait-card__avatar">${member.avatar && member.avatar.length <= 2 ? escapeHtml(member.avatar) : Icon.user}</div>
      <div class="family-portrait-card__info">
        <div class="family-portrait-card__name">${escapeHtml(member.relation || "")}</div>
        <div class="tag-row" style="margin-top:6px">
          ${tags.length
            ? tags.map((t) => `<span class="tag tag--brand">${escapeHtml(t)}</span>`).join("")
            : `<span style="font-size:12px;color:var(--ink-3)">暂无特征标签</span>`
          }
        </div>
      </div>
    </div>`;
}

function _convItem(msg) {
  const role    = msg.role || "user";
  const isAI    = role === "assistant";
  const label   = isAI ? "AI" : "我";
  const content = String(msg.content || "").slice(0, 400);
  return `
    <div class="conv-item">
      <div class="conv-item__role ${isAI ? "conv-item__role--ai" : ""}">${escapeHtml(label)}</div>
      <div class="conv-item__text">${escapeHtml(content)}${String(msg.content || "").length > 400 ? "…" : ""}</div>
    </div>`;
}

function _empty(title, sub) {
  return `<div class="empty-state" style="padding:20px 0"><strong>${escapeHtml(title)}</strong><span>${escapeHtml(sub)}</span></div>`;
}

// ── 事件 ──────────────────────────────────────────────────────────────────────

function _bindEvents(root) {
  qs("#memory-back", root)?.addEventListener("click", () => {
    import("../main.js").then((m) => m.navigate?.("profile")).catch(() => {});
  });

  qs("#clear-session-btn", root)?.addEventListener("click", async () => {
    if (!confirm("确认清空短期上下文？这将重置当前会话记忆。")) return;
    try {
      await memoryApi.clearSession();
      clearChatState();
      toast("短期上下文已清空");
      await renderMemory(root);
    } catch (err) {
      toast(err.message || "清空失败");
    }
  });
}
