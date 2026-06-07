/**
 * icons.js — 统一 SVG 图标库，各视图直接 import 使用。
 * 所有图标以 inline SVG 方式嵌入，无需额外 HTTP 请求。
 */

const _s = (d, extra = "") =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" ${extra}>${d}</svg>`;

export const Icon = {
  // 导航 / 功能
  brain:    _s('<path d="M9.5 2A3.5 3.5 0 006 5.5a3.5 3.5 0 00-3.5 3.5A3.5 3.5 0 006 12.5V20h12v-7.5a3.5 3.5 0 003.5-3.5A3.5 3.5 0 0018 5.5a3.5 3.5 0 00-3.5-3.5h-5z"/>'),
  sparkle:  _s('<path d="M12 3v1M12 20v1M4.22 4.22l.7.7M19.07 19.07l.7.7M3 12h1M20 12h1M4.22 19.78l.7-.7M19.07 4.93l-.7.7"/><circle cx="12" cy="12" r="3"/>'),
  // 地点类别
  landmark: _s('<line x1="3" y1="22" x2="21" y2="22"/><line x1="6" y1="18" x2="6" y2="11"/><line x1="10" y1="18" x2="10" y2="11"/><line x1="14" y1="18" x2="14" y2="11"/><line x1="18" y1="18" x2="18" y2="11"/><polygon points="12 2 20 7 4 7"/>'),
  utensils: _s('<path d="M3 2v7c0 1.1.9 2 2 2h4a2 2 0 002-2V2"/><path d="M7 2v20"/><path d="M21 15V2a5 5 0 00-5 5v6c0 1.1.9 2 2 2h3v7"/>'),
  coffee:   _s('<path d="M17 8h1a4 4 0 010 8h-1"/><path d="M3 8h14v9a4 4 0 01-4 4H7a4 4 0 01-4-4z"/><line x1="6" y1="2" x2="6" y2="4"/><line x1="10" y1="2" x2="10" y2="4"/><line x1="14" y1="2" x2="14" y2="4"/>'),
  wine:     _s('<path d="M8 22h8M7 10h10M12 15v7M17 2H7l-2 8h14z"/>'),
  mapPin:   _s('<circle cx="12" cy="10" r="3"/><path d="M12 2a8 8 0 00-8 8c0 4.5 7 12 8 12s8-7.5 8-12a8 8 0 00-8-8z"/>'),
  // 用户 / 人员
  user:     _s('<circle cx="12" cy="8" r="4"/><path d="M4 20c0-4 3.6-7 8-7s8 3 8 7"/>'),
  users:    _s('<circle cx="9" cy="7" r="3"/><circle cx="16.5" cy="9" r="2"/><path d="M2 20c0-3.3 3.1-5.5 7-5.5s7 2.2 7 5.5"/><path d="M18.5 14.5c1.7 0 3 1.3 3 3"/>'),
  // 动作 / 状态
  phone:    _s('<path d="M22 16.9v3a2 2 0 01-2.2 2 19.8 19.8 0 01-8.6-3.1 19.5 19.5 0 01-5.3-5.3 19.8 19.8 0 01-3.1-8.7A2 2 0 014.7 3h3a2 2 0 012 1.7 12.8 12.8 0 00.7 2.8 2 2 0 01-.5 2.1L9.1 10.4a16 16 0 006.1 6.1l.7-.8a2 2 0 012.1-.4 12.8 12.8 0 002.8.7A2 2 0 0122 17z"/>'),
  calendar: _s('<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>'),
  warn:     _s('<path d="M10.3 3.3L2 21h20L13.7 3.3a2 2 0 00-3.4 0z"/><line x1="12" y1="9" x2="12" y2="13"/><circle cx="12" cy="17" r="1" fill="currentColor" stroke="none"/>'),
  info:     _s('<circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><circle cx="12" cy="16" r="1" fill="currentColor" stroke="none"/>'),
  check:    _s('<polyline points="20 6 9 17 4 12"/>'),
  star:     _s('<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>'),
  memory:   _s('<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.7-4 3-9 3s-9-1.3-9-3"/><path d="M3 5v14c0 1.7 4 3 9 3s9-1.3 9-3V5"/>'),
  chat:     _s('<path d="M12 2C8 2 4 5.5 4 10c0 3 1.5 5.5 3.5 7L7 20l3.5-1.5c.5.1 1 .2 1.5.2 4.4 0 8-3.6 8-8S16.4 2 12 2z"/>'),
  route:    _s('<circle cx="5" cy="5" r="2"/><circle cx="19" cy="19" r="2"/><path d="M7 5h8a4 4 0 014 4v1a4 4 0 01-4 4H9a4 4 0 00-4 4"/>'),
  // 具体尺寸版本（用于 CSS class 标准化）
  sm: (d) => _s(d, 'width="16" height="16"'),
  md: (d) => _s(d, 'width="20" height="20"'),
};
