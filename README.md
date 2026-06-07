# 半日闲 AI

![半日闲 AI](assets/main_logo.png)

半日闲 AI 是面向美团 Hackathon 2026 本地生活场景的短时活动执行 Agent。它不是简单的搜索推荐系统，而是把用户一句自然语言目标转化为可确认、可落地的本地生活执行方案：规划路线、检查天气和余位、生成预约/下单草稿、整理同行人通知，并在用户确认后推进执行。

## 项目亮点

- **从推荐到执行**：输出不止是“去哪儿”，还包含每个地点要做什么、为什么这么安排、是否需要预约、买什么以及如何通知同行人。
- **自然语言理解**：支持“明晚”“这周六”“老婆减肥”“孩子迷恐龙”“朋友有人不吃辣”等模糊且生活化的输入。
- **多方案评分**：先生成多个候选方案，再基于时间、动线、预算、餐饮匹配、同行人画像和可执行性进行评分，选择最稳方案。
- **真实工具链路**：集成地点库、天气、容量/余位、订单草稿、短信/通知限制说明、偏好记忆和 SSE 流式思考过程。
- **防重复履约**：地点卡片和一键执行共用动作状态，避免同一餐厅、商品或通知被重复预约/下单。
- **比赛展示友好**：Web 首页、产品 Demo、设计文档下载、测试报告和 Agent 设计说明均已整理。

## 在线体验入口

- 产品首页：`/`
- 产品 Demo：`/app`
- 设计文档：`/appdix/设计文档.pdf`
- GitHub 仓库：<https://github.com/yuanjianzhang0/banrixian>

## 技术架构

```text
用户自然语言目标
        ↓
LLM 意图理解 / 同行人画像 / 时间解析
        ↓
工具编排层：地点检索、天气、余位、记忆、订单、通知
        ↓
多候选路线生成与评分
        ↓
最终行程卡片 + 执行动作草稿 + 分享/通知文案
        ↓
用户确认后一键执行
```

核心模块：

- `main.py`：FastAPI 应用入口。
- `routers/`：认证、聊天、地图、记忆、订单、通知、分享等 API。
- `skills/`：天气、短信、记忆、运行时工具封装。
- `agent/`：Agent 规划器、提示词、LLM 客户端、工具注册和本地测试。
- `banrixian_web/`：产品官网与 Web Demo 前端。
- `static/place_images/`：本地场景图片缓存。
- `scripts/`：质量测试、系统 E2E、地点导入等脚本。

## 运行方式

安装 Python 依赖后，在项目根目录启动后端：

```bash
python main.py
```

如果使用 Nginx 部署静态页面，可将 `banrixian_web/` 同步到站点目录，并使用 `nginx/banrixian-web.conf` 作为参考配置。

## 环境变量

项目支持本地规则和 LLM 两种运行模式。生产或比赛演示建议配置：

```bash
LLM_PROVIDER=local
OPENAI_API_KEY=你的模型服务 Key
AMAP_WEB_API_KEY=你的高德 Web API Key
AMAP_API_KEY=你的高德服务 Key
```

短信接口在非企业认证场景下默认只展示合规限制说明，不会伪造真实发送结果。

## 测试与验证

已有测试脚本：

```bash
python scripts/run_route_quality_tests.py
python scripts/run_system_e2e_tests.py
```

测试报告位于：

- `TEST_REPORT_20260602.md`
- `test_logs/route_planning_quality_20260602.md`
- `test_logs/system_e2e_20260602.md`

## 设计说明

详细 Agent 设计可查看：

- `agent/README_AGENT.md`
- `banrixian_web/appdix/设计文档.pdf`

设计重点包括：

- Planning 策略：自然语言拆解、时间校准、偏好记忆读取、候选方案生成、多维评分和最终定稿。
- 工具调用链路：天气、地点、余位、订单、短信/通知、分享文案等工具按需调用。
- 异常处理机制：天气超时降级、短信企业认证限制提示、登录/验证码异常提示、重复预约保护和 SSE 过程可视化。

## 品牌与视觉

项目使用 `assets/main_logo.png` 和 `assets/slogan.png` 作为品牌素材。官网采用美团黄、深色主视觉和本地生活图片卡片，突出“帮用户把事情做完”的比赛主题。

