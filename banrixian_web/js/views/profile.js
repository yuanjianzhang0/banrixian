import { userApi } from "../api.js";
import { setSession, state } from "../state.js";
import { escapeHtml, qs, setLoading, toast } from "../ui.js";
import { Icon } from "../icons.js";

const PROFILE_EMOJIS = ["🧑", "👩", "👨", "🧔", "👩‍💼", "👨‍💼", "🎉", "🌟", "🐻", "🦊", "🐼", "🦁"];

export async function renderProfile(root) {
  setLoading(root, "正在加载个人中心");
  try {
    const res = await userApi.profile();
    const user = res?.data || res || state.user || {};
    // Sync state
    setSession(state.token, { ...state.user, ...user });
    root.innerHTML = buildProfileHTML(user);
    bindProfileEvents(root, user);
  } catch (err) {
    // Fallback to cached user
    const user = state.user || {};
    root.innerHTML = buildProfileHTML(user);
    bindProfileEvents(root, user);
  }
}

function buildProfileHTML(user) {
  const avatar  = user.avatar  || "🧑";
  const name    = user.name    || user.username || "用户";
  const points  = user.points  || user.score    || 0;
  const city    = user.city    || "";
  const phone   = user.phone   || "";
  const tags    = Array.isArray(user.tags) ? user.tags : [];

  const tagsHtml = tags.length
    ? `<div class="tag-row">${tags.map((t) => `<span class="tag tag--brand">${escapeHtml(t)}</span>`).join("")}</div>`
    : "";

  return `
    <div class="planner-wrap">
      <div class="profile-header">
        <div class="profile-avatar" id="profile-avatar-display">${avatar}</div>
        <div class="profile-name">${escapeHtml(name)}</div>
        <div class="profile-points">⭐ ${points} 积分</div>
        ${tagsHtml}
        <div style="margin-top:12px">
          <button id="sign-btn-profile" class="btn btn--primary btn--sm">签到 +50分</button>
        </div>
      </div>

      <div class="profile-quick-links">
        <button class="profile-link-btn" data-route="family">
          <span style="width:16px;height:16px;display:inline-flex">${Icon.users}</span> 亲友画像
        </button>
        <button class="profile-link-btn" data-route="memory">
          <span style="width:16px;height:16px;display:inline-flex">${Icon.memory}</span> 偏好记忆
        </button>
      </div>

      <div class="panel">
        <div class="panel__head">
          <h3>编辑资料</h3>
        </div>
        <form id="profile-form" class="form-stack">
          <div>
            <div class="section-label" style="margin-bottom:8px">头像</div>
            <div class="emoji-picker" id="profile-emoji-picker">
              ${PROFILE_EMOJIS.map((e) =>
                `<button type="button" class="emoji-btn${e === avatar ? " is-active" : ""}" data-emoji="${e}">${e}</button>`
              ).join("")}
            </div>
            <input type="hidden" id="profile-avatar-input" name="avatar" value="${escapeHtml(avatar)}" />
          </div>
          <label class="field-label">昵称
            <input name="name" value="${escapeHtml(name)}" placeholder="你的昵称" />
          </label>
          <label class="field-label">城市
            <input name="city" value="${escapeHtml(city)}" placeholder="例：北京" />
          </label>
          <label class="field-label">手机号
            <input name="phone" type="tel" value="${escapeHtml(phone)}" placeholder="选填" />
          </label>
          <label class="field-label">兴趣标签（逗号分隔）
            <input name="tags_input" value="${escapeHtml(tags.join(","))}" placeholder="例：爱美食,喜户外,亲子" />
          </label>
          <button type="submit" class="btn btn--primary" id="save-profile-btn">保存资料</button>
        </form>
      </div>
    </div>
  `;
}

function bindProfileEvents(root, user) {
  // Sign in button on profile page
  qs("#sign-btn-profile", root)?.addEventListener("click", async () => {
    const btn = qs("#sign-btn-profile", root);
    if (btn) { btn.disabled = true; btn.textContent = "签到中…"; }
    try {
      const res = await userApi.sign();
      toast(res.data?.msg || "签到成功 +50 积分");
      await renderProfile(root);
    } catch (err) {
      toast(err.message || "签到失败");
      if (btn) { btn.disabled = false; btn.textContent = "签到 +50分"; }
    }
  });

  // Quick links to family / memory
  qs(".profile-quick-links", root)?.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-route]");
    if (!btn) return;
    import("../main.js").then((m) => m.navigate?.(btn.dataset.route)).catch(() => {});
  });

  // Emoji picker
  qs("#profile-emoji-picker", root)?.addEventListener("click", (e) => {
    const btn = e.target.closest(".emoji-btn");
    if (!btn) return;
    const picker = qs("#profile-emoji-picker", root);
    picker?.querySelectorAll(".emoji-btn").forEach((b) => b.classList.remove("is-active"));
    btn.classList.add("is-active");
    const avatarInput = qs("#profile-avatar-input", root);
    if (avatarInput) avatarInput.value = btn.dataset.emoji;
    const avatarDisplay = qs("#profile-avatar-display", root);
    if (avatarDisplay) avatarDisplay.textContent = btn.dataset.emoji;
  });

  // Profile form submit
  qs("#profile-form", root)?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd    = new FormData(e.currentTarget);
    const tagsInput = (fd.get("tags_input") || "").toString();
    const tags  = tagsInput.split(",").map((t) => t.trim()).filter(Boolean);
    const payload = {
      name:   fd.get("name") || "",
      city:   fd.get("city") || "",
      phone:  fd.get("phone") || "",
      avatar: fd.get("avatar") || "🧑",
      tags,
    };
    const btn = qs("#save-profile-btn", root);
    if (btn) { btn.disabled = true; btn.textContent = "保存中…"; }
    try {
      const res = await userApi.updateProfile(payload);
      const updated = res?.data || { ...state.user, ...payload };
      setSession(state.token, updated);
      toast("资料已保存");
      await renderProfile(root);
    } catch (err) {
      toast(err.message || "保存失败");
      if (btn) { btn.disabled = false; btn.textContent = "保存资料"; }
    }
  });
}
