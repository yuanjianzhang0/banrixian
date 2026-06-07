import { orderApi } from "../api.js";
import { escapeHtml, qs, setLoading, toast } from "../ui.js";
import { unlockBookedPlace } from "./chat.js?v=20260607-210000";
import { Icon } from "../icons.js";
import { state } from "../state.js";

const CITY_OPTIONS = ["全部", "北京", "上海", "广州", "深圳", "杭州", "成都", "南京", "武汉", "西安", "重庆", "天津"];
const ORDER_CITY_KEY = "brx.ordersCity";
const SELECTED_CITY_KEY = "brx.selectedCity";

function _currentOrderCity() {
  // 优先级：用户在行程页主动选过的城市 > 当前聊天所在城市 > 用户账号城市 > 全部
  const orderCity   = localStorage.getItem(ORDER_CITY_KEY);
  const selectedCity = localStorage.getItem(SELECTED_CITY_KEY);
  const userCity     = state.user?.city;
  return orderCity || selectedCity || userCity || "all";
}

export async function renderOrders(root) {
  setLoading(root, "正在读取行程");
  try {
    const city = _currentOrderCity();
    const res = await orderApi.list("all", city === "全部" ? "all" : city);
    const orders = Array.isArray(res?.data) ? res.data : [];
    root.innerHTML = _buildHTML(orders, city);
    _bindEvents(root, orders);
  } catch (err) {
    root.innerHTML = `<div class="empty-state"><strong>加载失败</strong><span>${escapeHtml(err.message)}</span></div>`;
  }
}

function _cityOptionsHtml(current) {
  return CITY_OPTIONS.map((city) => {
    const value = city === "全部" ? "all" : city;
    const selected = value === current || city === current ? " selected" : "";
    return `<option value="${escapeHtml(value)}"${selected}>${escapeHtml(city)}</option>`;
  }).join("");
}

function _buildHTML(orders, currentCity = "all") {
  const citySelectHtml = `
    <label class="orders-city-picker">
      <span>城市</span>
      <select id="orders-city">${_cityOptionsHtml(currentCity)}</select>
    </label>`;

  if (!orders.length) {
    return `
      <div class="planner-wrap">
        <div class="orders-title-row">
          <h2 class="planner-heading">我的行程</h2>
          ${citySelectHtml}
        </div>
        <div class="empty-state" style="margin-top:24px">
          <span style="width:48px;height:48px;display:flex;align-items:center;justify-content:center;color:var(--ink-3)">${Icon.calendar}</span>
          <strong>暂无行程记录</strong>
          <span>前往「AI 规划」生成路线后可预约地点</span>
          <button class="btn btn--primary" data-route="chat" style="margin-top:12px">去规划</button>
        </div>
      </div>`;
  }

  // 按 plan_summary + date 分组
  const groups = _groupOrders(orders);

  const groupsHtml = groups.map((g) => `
    <div class="order-group">
      <div class="order-group__header">
        <div class="order-group__title">${escapeHtml(g.title)}</div>
        <div class="order-group__date">${escapeHtml(g.date)}</div>
      </div>
      ${g.orders.map(_buildCard).join("")}
    </div>
  `).join("");

  return `
    <div class="planner-wrap">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px">
        <div>
          <h2 class="planner-heading">我的行程</h2>
          <p class="planner-subtext">已预约的地点，按规划时间分组</p>
        </div>
        <div class="orders-toolbar">
          ${citySelectHtml}
          <button id="refresh-orders" class="btn btn--outline btn--sm">刷新</button>
        </div>
      </div>
      <div id="orders-list">${groupsHtml}</div>
    </div>`;
}

function _groupOrders(orders) {
  // 先按 plan_summary + date_str 分组
  const map = new Map();
  for (const o of orders) {
    const key = (o.plan_summary || "") + "|" + (o.date_str || "");
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(o);
  }
  const groups = [];
  for (const [key, list] of map) {
    const first = list[0];
    const summary = (first.plan_summary || "").slice(0, 40);
    const city = first.city || "北京";
    const dateStr  = first.date_str || "";
    const timeStr  = first.created_at ? first.created_at.slice(0, 16) : dateStr;
    groups.push({
      title: summary || "AI 规划路线",
      date:  `${city} · ${timeStr}`,
      orders: list,
    });
  }
  return groups;
}

function _buildCard(order) {
  // 地点名：优先 title（后端存的就是 place_name → title）
  const name   = order.title || order.place_name || order.name || "未知地点";
  const desc   = order.desc  || "";
  const price  = order.price || "";
  const tags   = Array.isArray(order.tags) ? order.tags : [];
  const city   = order.city || "北京";

  return `
    <div class="order-card" data-id="${escapeHtml(String(order.id || ""))}">
      <div class="order-card__top">
        <div style="flex:1;min-width:0">
          <div class="order-card__name">${escapeHtml(name)}</div>
          <div class="order-card__meta">
            ${escapeHtml(desc)}${price ? ` · ${escapeHtml(price)}` : ""}
          </div>
          ${tags.length ? `<div class="tag-row" style="margin-top:6px">${tags.map((t) => `<span class="tag">${escapeHtml(t)}</span>`).join("")}</div>` : ""}
          <div class="tag-row" style="margin-top:6px"><span class="tag tag--green">${escapeHtml(city)}</span></div>
        </div>
        <button class="btn btn--ghost btn--sm delete-order-btn"
          data-id="${escapeHtml(String(order.id || ""))}"
          style="color:var(--red);flex-shrink:0;align-self:flex-start">
          删除
        </button>
      </div>
      <div class="order-card__footer">
        <span class="order-status order-status--ongoing">进行中</span>
      </div>
    </div>`;
}

function _bindEvents(root, orders) {
  qs("#refresh-orders", root)?.addEventListener("click", () => renderOrders(root));
  qs("#orders-city", root)?.addEventListener("change", (e) => {
    const city = e.target.value || "all";
    localStorage.setItem(ORDER_CITY_KEY, city);
    renderOrders(root);
  });

  qs("[data-route='chat']", root)?.addEventListener("click", () => {
    import("../main.js").then((m) => m.navigate?.("chat")).catch(() => {});
  });

  qs("#orders-list", root)?.addEventListener("click", async (e) => {
    const btn = e.target.closest(".delete-order-btn");
    if (!btn) return;
    const id = btn.dataset.id;
    if (!id || !confirm("确认删除此条行程？")) return;
    const order = orders.find((item) => String(item.id || "") === String(id));
    const placeName = order?.title || order?.place_name || order?.name || "";
    const hasSamePlaceAfterDelete = placeName
      ? orders.some((item) =>
          String(item.id || "") !== String(id)
          && (item.title || item.place_name || item.name || "") === placeName
        )
      : false;
    btn.disabled = true; btn.textContent = "删除中…";
    try {
      await orderApi.delete(id);
      if (placeName && !hasSamePlaceAfterDelete) unlockBookedPlace(placeName);
      toast("已删除");
      await renderOrders(root);
    } catch (err) {
      toast(err.message || "删除失败");
      btn.disabled = false; btn.textContent = "删除";
    }
  });
}
