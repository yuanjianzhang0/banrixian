import { contentApi, orderApi, userApi } from "../api.js";
import { emptyState, escapeHtml, panel, setLoading, tagsHtml } from "../ui.js";

const SELECTED_CITY_KEY = "brx.selectedCity";

function currentCity() {
  return localStorage.getItem(SELECTED_CITY_KEY) || "北京";
}

export async function renderDashboard(root) {
  setLoading(root, "正在汇总工作台");
  const city = currentCity();
  const results = await Promise.allSettled([
    userApi.profile(),
    contentApi.services(),
    contentApi.nearby(),
    contentApi.hotlist(),
    contentApi.topRoutes(),
    orderApi.list("all", city),
    contentApi.weather({ city }),
  ]);

  const [profile, services, nearby, hotlist, routes, orders, weather] = results.map((item) =>
    item.status === "fulfilled" ? item.value?.data : null,
  );

  const orderList = Array.isArray(orders) ? orders : [];
  root.innerHTML = `
    <section class="kpi-grid">
      ${kpi("用户积分", profile?.pts ?? "--", profile?.name || "当前用户")}
      ${kpi("服务品类", count(services), "酒店、门票、美食等")}
      ${kpi("进行订单", orderList.length, "来自 AI 或服务预订")}
      ${kpi("天气状态", weather?.status === "ok" ? `${weather.temperature_c ?? "--"}℃` : "--", weather?.weather || "实时天气")}
    </section>

    <section class="content-grid">
      ${panel("实时路线素材", nearbyList(nearby))}
      ${panel("热榜与服务", hotAndServices(hotlist, services))}
    </section>

    ${panel("热门 AI 路线", routeList(routes))}
  `;
}

function count(value) {
  return Array.isArray(value) ? value.length : "--";
}

function kpi(label, value, detail) {
  return `<div class="kpi"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><span>${escapeHtml(detail)}</span></div>`;
}

function nearbyList(rows) {
  if (!Array.isArray(rows) || !rows.length) return emptyState("暂无附近 POI");
  return `<div class="card-list">${rows
    .map(
      (item) => `
        <article class="data-card">
          <div class="data-card__row"><strong>${escapeHtml(item.ico || "📍")} ${escapeHtml(item.name)}</strong><span>${escapeHtml(item.score || "")}</span></div>
          <div class="tag-row">${tagsHtml([item.tags, item.capacity_status ? `状态 ${item.capacity_status}` : ""].filter(Boolean))}</div>
        </article>
      `,
    )
    .join("")}</div>`;
}

function hotAndServices(hotlist, services) {
  const hot = Array.isArray(hotlist) ? hotlist.slice(0, 4) : [];
  const svc = Array.isArray(services) ? services.slice(0, 3) : [];
  return `
    <div class="card-list">
      ${hot
        .map((item) => `<article class="data-card"><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.desc || "")}</small></article>`)
        .join("") || emptyState("暂无热榜")}
      ${svc
        .map((item) => `<article class="data-card"><strong>${escapeHtml(item.ico || "")} ${escapeHtml(item.name)}</strong><small>${escapeHtml(item.desc || "")}</small></article>`)
        .join("")}
    </div>
  `;
}

function routeList(routes) {
  if (!Array.isArray(routes) || !routes.length) return emptyState("暂无热门路线");
  return `<div class="card-list">${routes
    .map((route) => {
      const steps = Array.isArray(route.route_data) ? route.route_data : [];
      return `
        <article class="data-card">
          <div class="data-card__row"><strong>${escapeHtml(route.title)}</strong><span class="tag tag--green">${escapeHtml(route.keyword || "路线")}</span></div>
          <div class="timeline">
            ${steps
              .slice(0, 4)
              .map(
                (step) => `
                  <div class="timeline__item">
                    <span class="timeline__time">${escapeHtml(step.time || "--")}</span>
                    <div><strong>${escapeHtml(step.name)}</strong><small>${escapeHtml(step.meta || "")}</small></div>
                  </div>
                `,
              )
              .join("")}
          </div>
        </article>
      `;
    })
    .join("")}</div>`;
}
