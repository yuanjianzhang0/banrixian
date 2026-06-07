import { userApi } from "../api.js";
import { emptyState, escapeHtml, qs, qsa, setLoading, toast } from "../ui.js";
import { state } from "../state.js";
import { Icon } from "../icons.js";

const AVATAR_EMOJIS = ["👩", "👨", "👦", "👧", "🧒", "👴", "👵", "👥", "🧑", "👶", "🧓", "🧔"];

const TAG_COLORS = [
  "tag--clay", "tag--green", "tag--brand", "tag--blue",
  "", "", "", "",
];

function tagColorClass(idx) {
  return TAG_COLORS[idx % TAG_COLORS.length] || "";
}

export async function renderFamily(root) {
  setLoading(root, "正在读取亲友库");
  try {
    const res = await userApi.family();
    const members = Array.isArray(res?.data) ? res.data : [];
    root.innerHTML = buildFamilyHTML(members);
    bindFamilyEvents(root, members);
  } catch (err) {
    root.innerHTML = `<div class="empty-state"><strong>加载失败</strong><span>${escapeHtml(err.message)}</span></div>`;
  }
}

function buildFamilyHTML(members) {
  const backBtn = state.previousRoute === "profile"
    ? `<button class="page-back-btn" data-back="profile">← 返回个人中心</button>`
    : "";

  const cardsHtml = members.length
    ? members.map((m) => buildMemberCard(m)).join("")
    : `<div class="empty-state" style="padding:32px 0"><strong>暂无亲友</strong><span>添加亲友后，AI 规划时会自动考虑他们的偏好</span></div>`;

  return `
    <div class="planner-wrap">
      ${backBtn}
      <div>
        <h2 class="planner-heading">亲友画像</h2>
        <p class="planner-subtext">AI 自动从对话中提取，也可手动添加维护</p>
      </div>

      <div id="family-list">${cardsHtml}</div>

      <div class="panel">
        <div class="panel__head"><h3>添加亲友</h3></div>
        <form id="add-family-form" class="form-stack">
          <div>
            <div class="section-label" style="margin-bottom:8px">选择头像</div>
            <div class="emoji-picker" id="emoji-picker">
              ${AVATAR_EMOJIS.map((e, i) =>
                `<button type="button" class="emoji-btn${i === 0 ? " is-active" : ""}" data-emoji="${e}">${e}</button>`
              ).join("")}
            </div>
          </div>
          <label class="field-label">称呼 / 关系
            <input name="relation" required placeholder="例：老婆、儿子、妈妈、朋友" />
          </label>
          <div class="field-label">
            <span>特征</span>
            <div class="tag-builder" id="add-tag-builder">
              <div class="family-chip-row" id="add-tag-list"></div>
              <div class="tag-add-row">
                <input id="add-tag-input" placeholder="例：喜欢美食、不能走太远、5岁" />
                <button type="button" class="tag-add-btn" id="add-tag-btn">+</button>
              </div>
            </div>
          </div>
          <input type="hidden" name="avatar" id="selected-avatar" value="${AVATAR_EMOJIS[0]}" />
          <button type="submit" class="btn btn--primary">添加亲友</button>
        </form>
      </div>

      <div class="muted" style="padding:4px 0;text-align:center">
        AI 自动从对话提取亲友信息，无需全部手动录入
      </div>
    </div>
  `;
}

function buildMemberCard(m) {
  const tags = Array.isArray(m.tags) ? m.tags : [];
  const displayName = m.name || m.relation || "亲友";
  const relationText = m.name && m.relation ? m.relation : "亲友画像";
  const tagsHtml = tags.map((t, i) =>
    `<span class="family-tag-chip ${tagColorClass(i)}">
      <span>${escapeHtml(t)}</span>
      <button type="button" class="family-tag-chip__remove" data-id="${m.id}" data-tag="${escapeHtml(t)}" title="删除特征">×</button>
    </span>`
  ).join("");

  return `
    <div class="family-card" data-id="${m.id}">
      <div class="family-card__top">
        <div class="family-avatar">${m.avatar && /\p{Emoji}/u.test(m.avatar) ? escapeHtml(m.avatar) : Icon.users}</div>
        <div class="family-card__info">
          <div class="family-card__name">${escapeHtml(displayName)}</div>
          <div class="family-card__relation">${escapeHtml(relationText)}</div>
        </div>
        <div class="family-card__actions">
          <button type="button" class="tag-add-square add-member-tag-btn" data-id="${m.id}" title="添加特征">+</button>
          <button type="button" class="btn btn--ghost btn--sm delete-btn" data-id="${m.id}" style="color:var(--red)">删除</button>
        </div>
      </div>
      <div class="family-card__tags" data-tags-for="${m.id}">
        ${tagsHtml || `<span class="family-tag-empty">暂无特征</span>`}
      </div>
      <div class="family-card__tag-edit is-hidden" id="tag-edit-${m.id}">
        <div class="tag-add-row">
          <input class="tag-edit-input" data-id="${m.id}" placeholder="输入一个新特征" />
          <button class="tag-add-btn save-tags-btn" data-id="${m.id}" type="button">+</button>
          <button class="btn btn--ghost btn--sm cancel-tags-btn" data-id="${m.id}" type="button">取消</button>
        </div>
      </div>
    </div>
  `;
}

function getMember(members, id) {
  return members.find((m) => String(m.id) === String(id));
}

function uniqueTags(tags) {
  return [...new Set((tags || []).map((t) => String(t || "").trim()).filter(Boolean))].slice(0, 20);
}

async function saveMemberTags(root, members, id, tags, busyEl = null) {
  const member = getMember(members, id);
  if (!member) return;
  if (busyEl) busyEl.disabled = true;
  try {
    await userApi.updateFamily(id, {
      relation: member.relation,
      avatar: member.avatar,
      tags: uniqueTags(tags),
    });
    toast("特征已更新");
    await renderFamily(root);
  } catch (err) {
    toast(err.message || "更新失败");
    if (busyEl) busyEl.disabled = false;
  }
}

function bindFamilyEvents(root, members) {
  let draftTags = [];
  const renderDraftTags = () => {
    const list = qs("#add-tag-list", root);
    if (!list) return;
    list.innerHTML = draftTags.map((tag) => `
      <span class="family-tag-chip">
        <span>${escapeHtml(tag)}</span>
        <button type="button" class="family-tag-chip__remove draft-tag-remove" data-tag="${escapeHtml(tag)}">×</button>
      </span>
    `).join("");
  };
  const addDraftTag = () => {
    const input = qs("#add-tag-input", root);
    const tag = (input?.value || "").trim();
    if (!tag) return;
    draftTags = uniqueTags([...draftTags, tag]);
    if (input) input.value = "";
    renderDraftTags();
  };

  // 返回按钮
  qs(".page-back-btn", root)?.addEventListener("click", () => {
    import("../main.js").then((m) => m.navigate?.("profile")).catch(() => {});
  });

  // Emoji picker in add form
  qs("#emoji-picker", root)?.addEventListener("click", (e) => {
    const btn = e.target.closest(".emoji-btn");
    if (!btn) return;
    qsa(".emoji-btn", root).forEach((b) => b.classList.remove("is-active"));
    btn.classList.add("is-active");
    const avatarInput = qs("#selected-avatar", root);
    if (avatarInput) avatarInput.value = btn.dataset.emoji;
  });

  qs("#add-tag-btn", root)?.addEventListener("click", addDraftTag);
  qs("#add-tag-input", root)?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      addDraftTag();
    }
  });
  qs("#add-tag-list", root)?.addEventListener("click", (e) => {
    const removeBtn = e.target.closest(".draft-tag-remove");
    if (!removeBtn) return;
    draftTags = draftTags.filter((tag) => tag !== removeBtn.dataset.tag);
    renderDraftTags();
  });

  // Add family form
  qs("#add-family-form", root)?.addEventListener("submit", async (e) => {
    e.preventDefault();
    addDraftTag();
    const fd = new FormData(e.currentTarget);
    const payload = {
      relation: fd.get("relation"),
      avatar:   fd.get("avatar") || AVATAR_EMOJIS[0],
      tags:     draftTags,
    };
    const btn = qs('[type="submit"]', e.currentTarget);
    if (btn) { btn.disabled = true; btn.textContent = "添加中…"; }
    try {
      await userApi.addFamily(payload);
      toast("亲友已添加");
      await renderFamily(root);
    } catch (err) {
      toast(err.message);
      if (btn) { btn.disabled = false; btn.textContent = "添加亲友"; }
    }
  });

  // Tag chips: remove one by one, or add one by one.
  qs("#family-list", root)?.addEventListener("click", async (e) => {
    const addBtn    = e.target.closest(".add-member-tag-btn");
    const removeTag = e.target.closest(".family-tag-chip__remove:not(.draft-tag-remove)");
    const deleteBtn = e.target.closest(".delete-btn");
    const saveBtn   = e.target.closest(".save-tags-btn");
    const cancelBtn = e.target.closest(".cancel-tags-btn");

    if (addBtn) {
      const id = addBtn.dataset.id;
      const editArea = qs(`#tag-edit-${id}`, root);
      editArea?.classList.toggle("is-hidden");
      qs(`.tag-edit-input[data-id="${id}"]`, root)?.focus();
      return;
    }

    if (removeTag) {
      const id = removeTag.dataset.id;
      const member = getMember(members, id);
      if (!member) return;
      const tags = uniqueTags(member.tags).filter((tag) => tag !== removeTag.dataset.tag);
      await saveMemberTags(root, members, id, tags, removeTag);
      return;
    }

    if (cancelBtn) {
      const id = cancelBtn.dataset.id;
      qs(`#tag-edit-${id}`, root)?.classList.add("is-hidden");
      const input = qs(`.tag-edit-input[data-id="${id}"]`, root);
      if (input) input.value = "";
      return;
    }

    if (saveBtn) {
      const id = saveBtn.dataset.id;
      const input = qs(`.tag-edit-input[data-id="${id}"]`, root);
      const tag = (input?.value || "").trim();
      if (!tag) { toast("请输入一个特征"); return; }
      const member = getMember(members, id);
      if (!member) return;
      const tags = uniqueTags([...(member.tags || []), tag]);
      await saveMemberTags(root, members, id, tags, saveBtn);
      return;
    }

    if (deleteBtn) {
      const id = deleteBtn.dataset.id;
      if (!confirm("确认删除此亲友？")) return;
      deleteBtn.disabled = true;
      try {
        await userApi.deleteFamily(id);
        toast("已删除");
        await renderFamily(root);
      } catch (err) {
        toast(err.message);
        deleteBtn.disabled = false;
      }
    }
  });

  qs("#family-list", root)?.addEventListener("keydown", async (e) => {
    if (e.key !== "Enter") return;
    const input = e.target.closest(".tag-edit-input");
    if (!input) return;
    e.preventDefault();
    const id = input.dataset.id;
    const tag = (input.value || "").trim();
    if (!tag) { toast("请输入一个特征"); return; }
    const member = getMember(members, id);
    if (!member) return;
    await saveMemberTags(root, members, id, uniqueTags([...(member.tags || []), tag]), input);
  });
}
