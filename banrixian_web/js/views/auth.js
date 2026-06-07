import { authApi } from "../api.js";
import { setSession } from "../state.js";
import { formData, qs, qsa, readableMessage, toast } from "../ui.js";

const captchaStates = new WeakMap();
let pendingAuth = null;

function _emptyCaptchaState() {
  return {
    id: "",
    target: 0,
    width: 360,
    height: 168,
    pieceSize: 46,
    pieceTop: 48,
    image: "",
    startX: 0,
    x: 0,
    dragging: false,
    passed: false,
    startedAt: 0,
    elapsed: 0,
    trail: [],
  };
}

function _captchaState(node) {
  return node ? captchaStates.get(node) : null;
}

function _captchaPayload(node) {
  const state = _captchaState(node);
  if (!state?.passed) return null;
  return {
    id: state.id,
    x: Math.round(state.x),
    elapsed: state.elapsed,
    trail: state.trail.slice(-200),
  };
}

function _renderCaptchaBox(node, message = "拖动滑块完成验证") {
  const state = _captchaState(node);
  if (!node || !state) return;
  node.classList.remove("is-hidden");
  node.classList.toggle("is-passed", state.passed === true);
  node.innerHTML = `
    <div class="slider-captcha__label">
      <span>${message}</span>
      <button class="slider-captcha__refresh" type="button">刷新</button>
    </div>
    <div class="slider-captcha__stage"
      style="--captcha-target:${state.target}px;--captcha-piece-top:${state.pieceTop}px;--captcha-piece-size:${state.pieceSize}px;--captcha-image:url('${state.image}')">
      <div class="slider-captcha__image"></div>
      <div class="slider-captcha__target"></div>
      <div class="slider-captcha__piece"></div>
    </div>
      <div class="slider-captcha__track">
        <div class="slider-captcha__fill"></div>
        <button class="slider-captcha__handle" type="button" aria-label="拖动验证码滑块">›</button>
        <span class="slider-captcha__hint">${state.passed ? "验证通过" : "按住滑块，拖到缺口处"}</span>
      </div>`;

  const handle = qs(".slider-captcha__handle", node);
  const fill = qs(".slider-captcha__fill", node);
  const hint = qs(".slider-captcha__hint", node);
  const track = qs(".slider-captcha__track", node);
  const piece = qs(".slider-captcha__piece", node);
  if (piece) {
    piece.style.backgroundSize = `${state.width}px ${state.height}px`;
    piece.style.backgroundPosition = `${-state.target}px ${-state.pieceTop}px`;
  }

  const paint = () => {
    const current = _captchaState(node);
    if (!current) return;
    const max = Math.max(0, (track?.clientWidth || current.width) - 44);
    const x = Math.max(0, Math.min(current.x, max));
    if (handle) handle.style.transform = `translateX(${x}px)`;
    if (piece) piece.style.transform = `translateX(${x}px)`;
    if (fill) fill.style.width = `${x + 22}px`;
    if (hint && current.passed) hint.textContent = "验证通过";
  };

  const resetPosition = () => {
    const current = _captchaState(node);
    if (!current) return;
    current.x = 0;
    current.dragging = false;
    current.passed = false;
    current.startedAt = 0;
    current.elapsed = 0;
    current.trail = [];
    node.classList.remove("is-passed");
    node.classList.add("is-resetting");
    if (hint) hint.textContent = "按住滑块，拖到缺口处";
    requestAnimationFrame(() => {
      paint();
      window.setTimeout(() => node.classList.remove("is-resetting"), 260);
    });
  };

  const pointX = (event) => event.touches?.[0]?.clientX ?? event.clientX;
  const start = (event) => {
    const current = _captchaState(node);
    if (!current || current.passed) return;
    current.dragging = true;
    current.startX = pointX(event);
    current.startedAt = Date.now();
    current.trail = [{ x: 0, y: 0, t: 0 }];
    event.preventDefault();
  };
  const move = (event) => {
    const current = _captchaState(node);
    if (!current?.dragging) return;
    const max = Math.max(0, (track?.clientWidth || current.width) - 44);
    const rawX = pointX(event) - current.startX;
    current.x = Math.max(0, Math.min(rawX, max));
    current.elapsed = Date.now() - current.startedAt;
    current.trail.push({
      x: Math.round(current.x),
      y: Math.round((Math.random() - 0.5) * 4),
      t: current.elapsed,
    });
    paint();
    event.preventDefault();
  };
  const end = () => {
    const current = _captchaState(node);
    if (!current?.dragging) return;
    current.dragging = false;
    current.elapsed = Date.now() - current.startedAt;
    const ok = Math.abs(current.x - current.target) <= 7 && current.elapsed >= 450;
    current.passed = ok;
    if (!ok) {
      toast("滑块位置不正确，请重试");
      resetPosition();
    } else {
      node.classList.add("is-passed");
      paint();
      _continuePendingAuth();
    }
  };

  handle?.addEventListener("mousedown", start);
  handle?.addEventListener("touchstart", start, { passive: false });
  document.addEventListener("mousemove", move);
  document.addEventListener("touchmove", move, { passive: false });
  document.addEventListener("mouseup", end);
  document.addEventListener("touchend", end);
  qs(".slider-captcha__refresh", node)?.addEventListener("click", () => _loadCaptcha(node));
  paint();
}

async function _loadCaptcha(node, message) {
  const response = await authApi.captcha();
  const data = response.data || {};
  captchaStates.set(node, {
    ..._emptyCaptchaState(),
    id: data.id || "",
    target: Number(data.target || 0),
    width: Number(data.width || 360),
    height: Number(data.height || 168),
    pieceSize: Number(data.pieceSize || 46),
    pieceTop: Number(data.pieceTop || 48),
    image: data.image || "/assets/captcha-travel-bg.jpg",
  });
  _renderCaptchaBox(node, message);
}

async function _ensureCaptcha(node, message = "连续失败后需要滑块验证") {
  if (!node) return;
  const state = _captchaState(node);
  if (!state?.id || state?.passed) {
    await _loadCaptcha(node, message);
  } else {
    _renderCaptchaBox(node, message);
  }
}

function _hideCaptcha(node) {
  if (node) captchaStates.delete(node);
  node?.classList.add("is-hidden");
  if (node) node.innerHTML = "";
}

function _openCaptchaModal(message) {
  const modal = qs("#captcha-modal");
  const node = qs("#auth-captcha");
  modal?.classList.remove("is-hidden");
  if (node) {
    node.classList.remove("is-hidden", "is-passed");
    node.innerHTML = `
      <div class="slider-captcha__label">
        <span>${message || "正在加载验证码"}</span>
        <button class="slider-captcha__refresh" type="button" disabled>加载中</button>
      </div>
      <div class="slider-captcha__loading">正在加载图片验证码…</div>`;
  }
  return _loadCaptcha(node, message);
}

function _closeCaptchaModal() {
  const modal = qs("#captcha-modal");
  const node = qs("#auth-captcha");
  modal?.classList.add("is-hidden");
  if (node) captchaStates.delete(node);
  pendingAuth = null;
}

async function _continuePendingAuth() {
  if (!pendingAuth) return;
  const captchaNode = qs("#auth-captcha");
  const captcha = _captchaPayload(captchaNode);
  if (!captcha) return;

  const { mode, payload, onReady } = pendingAuth;
  pendingAuth = null;
  payload.captcha = captcha;
  try {
    if (mode === "login") {
      const response = await authApi.login(payload);
      setSession(response.data?.token, response.data?.user);
      toast("登录成功");
      _closeCaptchaModal();
      onReady();
    } else {
      await authApi.register(payload);
      toast("注册成功，请登录");
      _closeCaptchaModal();
      qsa("[data-auth-tab]")[0].click();
    }
  } catch (error) {
    const message = readableMessage(error);
    toast(message);
    if (mode === "login" && /用户名|密码/.test(message)) {
      _closeCaptchaModal();
      const form = qs("#login-form");
      const passwordInput = qs("input[name='password']", form);
      if (passwordInput) passwordInput.value = "";
      (qs("input[name='username']", form) || passwordInput)?.focus();
      return;
    }
    pendingAuth = { mode, payload, onReady };
    _openCaptchaModal("请重新完成滑块验证").catch((err) => toast(err.message || "验证码加载失败"));
  }
}

export function bindAuth(onReady) {
  qs("#captcha-modal-close")?.addEventListener("click", _closeCaptchaModal);
  qs("#captcha-modal")?.addEventListener("click", (event) => {
    if (event.target?.id === "captcha-modal") _closeCaptchaModal();
  });

  const startAuth = (form, mode) => {
    if (!form?.reportValidity?.()) return;
    const payload = formData(form);
    pendingAuth = { mode, payload, onReady };
    _openCaptchaModal(mode === "login" ? "请完成滑块验证后登录" : "请完成滑块验证后注册")
      .catch((err) => toast(err.message || "验证码加载失败"));
  };

  qsa("[data-auth-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      qsa("[data-auth-tab]").forEach((item) => item.classList.toggle("is-active", item === button));
      qs("#login-form").classList.toggle("is-hidden", button.dataset.authTab !== "login");
      qs("#register-form").classList.toggle("is-hidden", button.dataset.authTab !== "register");
    });
  });

  qs("#login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    startAuth(event.currentTarget, "login");
  });

  qs("#register-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    startAuth(event.currentTarget, "register");
  });

  qs("#login-form button[type='submit']")?.addEventListener("click", (event) => {
    event.preventDefault();
    startAuth(qs("#login-form"), "login");
  });

  qs("#register-form button[type='submit']")?.addEventListener("click", (event) => {
    event.preventDefault();
    startAuth(qs("#register-form"), "register");
  });
}
