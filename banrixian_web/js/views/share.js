/**
 * share.js — 扫码分享行程只读页
 * 无需登录，通过 ?share=xxx 访问
 */

import { shareApi } from "../api.js";
import { escapeHtml, qs } from "../ui.js";

const CAT_ICON = {
  default: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75"><circle cx="12" cy="10" r="3"/><path d="M12 2a8 8 0 00-8 8c0 4.5 7 12 8 12s8-7.5 8-12a8 8 0 00-8-8z"/></svg>`,
  食: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75"><path d="M3 2v7c0 1.1.9 2 2 2h4a2 2 0 002-2V2"/><path d="M7 2v20"/><path d="M21 15V2a5 5 0 00-5 5v6c0 1.1.9 2 2 2h3v7"/></svg>`,
  景: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75"><line x1="3" y1="22" x2="21" y2="22"/><line x1="6" y1="18" x2="6" y2="11"/><line x1="10" y1="18" x2="10" y2="11"/><line x1="14" y1="18" x2="14" y2="11"/><line x1="18" y1="18" x2="18" y2="11"/><polygon points="12 2 20 7 4 7"/></svg>`,
  咖: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75"><path d="M17 8h1a4 4 0 010 8h-1"/><path d="M3 8h14v9a4 4 0 01-4 4H7a4 4 0 01-4-4z"/></svg>`,
};

function _stepIcon(step) {
  const cat = String(step?.category || step?.name || "").slice(0, 1);
  return CAT_ICON[cat] || CAT_ICON.default;
}

function _firstArray(...values) {
  for (const value of values) {
    if (Array.isArray(value) && value.length) return value;
    if (value && typeof value === "object") {
      const nested = _firstArray(value.steps, value.items, value.route, value.itinerary);
      if (nested.length) return nested;
    }
  }
  return [];
}

function _normalizePlan(raw) {
  if (!raw || typeof raw !== "object") return {};
  if (raw.type === "plan" || raw.steps || raw.route || raw.itinerary || raw.action_bundle || raw.actions) return raw;
  return raw.plan || raw.data?.plan || raw.result?.plan || raw.final_plan || raw.finalPlan || raw;
}

function _normalizeStep(step) {
  const payload = step?.payload || {};
  const location = step?.location || step?.geo || payload.location || {};
  const lng = step?.lng ?? step?.longitude ?? location.lng ?? location.longitude ?? payload.lng ?? payload.longitude;
  const lat = step?.lat ?? step?.latitude ?? location.lat ?? location.latitude ?? payload.lat ?? payload.latitude;
  const start = step?.start_time || step?.startTime || payload.start_time || "";
  const end = step?.end_time || step?.endTime || payload.end_time || "";
  return {
    ...step,
    time: step?.time || (start && end ? `${start}-${end}` : start),
    name: step?.name || step?.place_name || step?.placeName || step?.title || step?.target || payload.place_name || payload.name || "未命名地点",
    category: step?.category || step?.type || payload.category || "",
    meta: step?.meta || step?.address || payload.address || step?.category || "",
    reason: step?.reason || step?.why || step?.description || step?.summary || payload.note || "",
    lng,
    lat,
  };
}

function _extractSteps(plan) {
  return _firstArray(
    plan.steps,
    plan.route,
    plan.itinerary,
    plan.schedule,
    plan.plan?.steps,
    plan.data?.steps,
    plan.result?.steps,
  ).map(_normalizeStep);
}

function _extractActions(plan) {
  return _firstArray(
    plan.action_bundle?.items,
    plan.execution_actions,
    plan.actions,
    plan.orders,
    plan.plan?.action_bundle?.items,
  );
}

function _renderActions(actions) {
  if (!Array.isArray(actions) || !actions.length) return "";
  const labels = {
    reserve: "预约",
    order: "下单",
    gift: "购买",
    sms: "通知",
    share: "分享",
    share_route: "分享行程",
    save_calendar: "日历提醒",
  };
  return `
    <div style="background:#fff;border-radius:14px;padding:16px 18px;margin-bottom:14px;box-shadow:0 1px 8px rgba(0,0,0,0.06)">
      <h3 style="margin:0 0 12px;font-size:14px;font-weight:700;color:#18181B">执行清单 · ${actions.length} 项</h3>
      ${actions.map((action) => {
        const payload = action?.payload || {};
        const type = action?.type || "";
        const target = action?.target || payload.place_name || payload.name || payload.item_name || payload.phone || "";
        const note = action?.message || action?.note || payload.note || payload.message || payload.item_name || "";
        return `
          <div style="padding:10px 0;border-bottom:1px solid #f4f4f4">
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:3px">
              <span style="background:#fff3ed;color:#D44208;border-radius:999px;padding:2px 8px;font-size:11px;font-weight:700">${escapeHtml(labels[type] || type || "动作")}</span>
              <strong style="font-size:13px;color:#18181B">${escapeHtml(target || "待确认")}</strong>
            </div>
            ${note ? `<p style="margin:0;font-size:12px;color:#888;line-height:1.55">${escapeHtml(note)}</p>` : ""}
          </div>`;
      }).join("")}
    </div>`;
}

function _renderSteps(steps) {
  if (!Array.isArray(steps) || !steps.length) {
    return `<p style="color:#888;font-size:13px;text-align:center;padding:16px">暂无路线步骤</p>`;
  }
  return steps.map((step, i) => `
    <div style="display:flex;gap:12px;padding:12px 0;border-bottom:1px solid #f0f0f0">
      <div style="display:flex;flex-direction:column;align-items:center;flex-shrink:0">
        <div style="width:36px;height:36px;border-radius:50%;background:#fff3ed;display:flex;align-items:center;justify-content:center;color:#D44208">
          ${_stepIcon(step)}
        </div>
        ${i < steps.length - 1 ? `<div style="width:2px;flex:1;background:#f0ebe6;margin:4px 0"></div>` : ""}
      </div>
      <div style="flex:1;min-width:0">
        <div style="display:flex;align-items:baseline;gap:8px;margin-bottom:3px">
          ${step.time ? `<span style="font-size:12px;font-weight:700;color:#D44208;flex-shrink:0">${escapeHtml(step.time)}</span>` : ""}
          <span style="font-size:15px;font-weight:600;color:#18181B;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(step.name || "")}</span>
        </div>
        ${step.meta ? `<p style="margin:0;font-size:12px;color:#888;line-height:1.5">${escapeHtml(step.meta)}</p>` : ""}
        ${step.reason ? `<p style="margin:4px 0 0;font-size:12px;color:#aaa;line-height:1.4">${escapeHtml(step.reason)}</p>` : ""}
      </div>
    </div>
  `).join("");
}

function _initShareMap(steps) {
  const mapEl = qs("#share-map");
  if (!mapEl || typeof AMap === "undefined") return;
  const validSteps = steps.filter((s) => s?.lng && s?.lat && (parseFloat(s.lng) || parseFloat(s.lat)));
  if (!validSteps.length) { mapEl.style.display = "none"; return; }
  try {
    const map = new AMap.Map("share-map", {
      zoom: 13,
      center: [parseFloat(validSteps[0].lng), parseFloat(validSteps[0].lat)],
    });
    validSteps.forEach((step, i) => {
      new AMap.Marker({
        map,
        position: [parseFloat(step.lng), parseFloat(step.lat)],
        label: { content: `<div style="background:#D44208;color:#fff;border-radius:4px;padding:2px 6px;font-size:11px;white-space:nowrap">${i + 1}. ${step.name || ""}</div>`, offset: new AMap.Pixel(0, -40) },
      });
    });
    if (validSteps.length > 1) {
      map.setFitView();
    }
  } catch (e) {}
}

export async function renderShare(root, shareId) {
  root.innerHTML = `
    <div style="max-width:480px;margin:0 auto;padding:0 16px 48px;font-family:-apple-system,BlinkMacSystemFont,'SF Pro Text',sans-serif">
      <div style="padding:24px 0 16px;text-align:center">
        <img src="/assets/main_logo.png" alt="半日闲AI" style="height:32px;object-fit:contain" onerror="this.style.display='none'" />
        <p style="margin:8px 0 0;font-size:13px;color:#888">半日出行规划</p>
      </div>
      <div id="share-loading" style="padding:40px;text-align:center;color:#888;font-size:14px">正在加载行程…</div>
      <div id="share-content" style="display:none"></div>
    </div>`;

  try {
    const res = await shareApi.getShare(shareId);
    const data = res?.data;
    if (!data?.plan) throw new Error("行程数据不存在");
    const plan = _normalizePlan(data.plan);
    const steps = _extractSteps(plan);
    const actions = _extractActions(plan);
    const intro = plan.intro || data.title || "行程方案";

    qs("#share-loading", root).style.display = "none";
    qs("#share-content", root).style.display = "";
    qs("#share-content", root).innerHTML = `
      <!-- 标题卡 -->
      <div style="background:#fff;border-radius:14px;padding:16px 18px;margin-bottom:14px;box-shadow:0 1px 8px rgba(0,0,0,0.06);border-left:4px solid #D44208">
        <p style="margin:0;font-size:15px;font-weight:600;color:#18181B;line-height:1.65">${escapeHtml(intro)}</p>
        ${plan.thinking?.length ? `
          <details style="margin-top:10px">
            <summary style="font-size:12px;color:#888;cursor:pointer">查看 AI 规划思路</summary>
            <ul style="margin:8px 0 0;padding-left:16px">
              ${plan.thinking.map((t) => `<li style="font-size:12px;color:#aaa;line-height:1.6;margin-bottom:3px">${escapeHtml(t)}</li>`).join("")}
            </ul>
          </details>
        ` : ""}
      </div>

      <!-- 地图 -->
      <div id="share-map" style="background:#f4f4f6;border-radius:12px;height:200px;margin-bottom:14px;overflow:hidden"></div>

      <!-- 步骤 -->
      <div style="background:#fff;border-radius:14px;padding:16px 18px;margin-bottom:14px;box-shadow:0 1px 8px rgba(0,0,0,0.06)">
        <h3 style="margin:0 0 12px;font-size:14px;font-weight:700;color:#18181B">行程安排 · ${steps.length} 个地点</h3>
        ${_renderSteps(steps)}
      </div>

      ${_renderActions(actions)}

      <!-- 推广 -->
      <div style="background:linear-gradient(135deg,#fff3ed,#fff8f4);border-radius:14px;padding:18px;text-align:center;border:1px solid #fde8db">
        <p style="margin:0 0 4px;font-size:14px;font-weight:600;color:#D44208">想生成属于你的路线？</p>
        <p style="margin:0 0 14px;font-size:13px;color:#888">半日闲AI · 一句话安排好出行</p>
        <a href="/" style="display:inline-block;background:#D44208;color:#fff;border-radius:10px;padding:10px 28px;font-size:14px;font-weight:600;text-decoration:none">
          立即体验
        </a>
      </div>`;

    setTimeout(() => _initShareMap(steps), 300);
  } catch (err) {
    qs("#share-loading", root).innerHTML = `
      <div style="padding:40px;text-align:center">
        <p style="font-size:15px;font-weight:600;color:#18181B;margin-bottom:8px">行程不存在</p>
        <p style="font-size:13px;color:#888;margin-bottom:20px">${escapeHtml(err.message || "链接可能已过期")}</p>
        <a href="/" style="background:#D44208;color:#fff;border-radius:10px;padding:10px 20px;font-size:14px;font-weight:600;text-decoration:none">返回首页</a>
      </div>`;
  }
}
