import { escapeHtml } from "../ui.js";

const _icon = (path) =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">${path}</svg>`;

const FEATURES = [
  {
    icon: _icon('<circle cx="5" cy="5" r="2"/><circle cx="19" cy="19" r="2"/><path d="M7 5h8a4 4 0 014 4v1a4 4 0 01-4 4H9a4 4 0 00-4 4"/>'),
    title: "一句话生成路线",
    desc: "描述需求，AI 自动安排时间、地点、顺序",
  },
  {
    icon: _icon('<circle cx="9" cy="7" r="3"/><circle cx="16.5" cy="9" r="2"/><path d="M2 20c0-3.3 3.1-5.5 7-5.5s7 2.2 7 5.5"/><path d="M18.5 14.5c1.7 0 3 1.3 3 3"/>'),
    title: "记住你的家人",
    desc: "孩子偏好、老人需求、伴侣忌口，自动记忆",
  },
  {
    icon: _icon('<circle cx="12" cy="10" r="3"/><path d="M12 2a8 8 0 00-8 8c0 4.5 7 12 8 12s8-7.5 8-12a8 8 0 00-8-8z"/>'),
    title: "真实地点库",
    desc: "地点来自真实数据，含坐标、评分、开放时间",
  },
  {
    icon: _icon('<path d="M17.5 10a5.5 5.5 0 10-10.6 2H6a4 4 0 000 8h12a4 4 0 000-8h-.9"/>'),
    title: "天气路线联动",
    desc: "实时天气影响选址，下雨优先室内备选",
  },
  {
    icon: _icon('<path d="M1 4v6h6"/><path d="M23 20v-6h-6"/><path d="M20.5 9A9 9 0 005.6 5.6L1 10M23 14l-4.6 4.4A9 9 0 013.5 15"/>'),
    title: "一键换一个",
    desc: "对某个地点不满意，单独替换，不影响其他",
  },
  {
    icon: _icon('<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/><path d="M9 16l2 2 4-4"/>'),
    title: "行程一键预约",
    desc: "生成后逐项预约，记录在「我的行程」",
  },
];

export async function renderShowcase(root) {
  root.innerHTML = `
    <div class="showcase-page">

      <!-- 标题区 -->
      <div class="showcase-hero">
        <div class="showcase-hero__eyebrow">半日闲 AI · 小程序演示</div>
        <h2 class="showcase-hero__title">一句话，安排好出行</h2>
        <p class="showcase-hero__sub">描述时间、同行人和偏好，AI 帮你生成真实路线、规避踩坑</p>
      </div>

      <!-- 视频播放器 -->
      <div class="showcase-video-wrap" id="showcase-video-wrap">
        <video
          id="showcase-video"
          class="showcase-video"
          controls
          playsinline
          preload="metadata"
          poster="/assets/demo_poster.jpg"
        >
          <source src="/assets/demo.mp4" type="video/mp4" />
          您的浏览器不支持视频播放
        </video>
        <div class="showcase-video-placeholder" id="video-placeholder">
          <div class="showcase-video-placeholder__icon">▶</div>
          <div class="showcase-video-placeholder__text">演示视频即将上线</div>
          <div class="showcase-video-placeholder__hint">视频文件上传至 /assets/demo.mp4 后自动显示</div>
        </div>
      </div>

      <!-- 功能亮点 -->
      <div class="showcase-features">
        <div class="showcase-features__title">核心功能</div>
        <div class="showcase-features__grid">
          ${FEATURES.map((f) => `
            <div class="showcase-feature-card">
              <div class="showcase-feature-card__icon">${f.icon}</div>
              <div class="showcase-feature-card__title">${escapeHtml(f.title)}</div>
              <div class="showcase-feature-card__desc">${escapeHtml(f.desc)}</div>
            </div>
          `).join("")}
        </div>
      </div>

      <!-- 扫码进入 -->
      <div class="showcase-qr-section">
        <div class="showcase-qr-section__text">
          <div class="showcase-qr-section__title">扫码立即体验</div>
          <p class="showcase-qr-section__desc">扫描二维码，在手机端打开半日闲 AI，获得最佳体验</p>
        </div>
        <div class="qr-target showcase-qr-section__code" id="showcase-qr"></div>
      </div>

    </div>
  `;

  // 视频已存在时直接显示，仅在真正出错时回退占位图
  const video = document.getElementById("showcase-video");
  const placeholder = document.getElementById("video-placeholder");
  if (video && placeholder) {
    // 立即隐藏占位图，让 video 元素直接呈现
    placeholder.style.display = "none";
    video.style.display = "block";

    // 只有视频真的加载失败（404/格式不支持）才显示占位图
    video.addEventListener("error", () => {
      video.style.display = "none";
      placeholder.style.display = "flex";
    });
    // 兼容 source 标签的错误
    const source = video.querySelector("source");
    if (source) {
      source.addEventListener("error", () => {
        video.style.display = "none";
        placeholder.style.display = "flex";
      });
    }
  }

  // 触发 QR 初始化
  if (typeof window.initQRCodes === "function") window.initQRCodes();
}
