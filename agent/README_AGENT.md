# 半日闲 Agent 说明

这个 `agent/` 是工具调用型 Agent，不是一次性 Prompt 包装器。

主流程：

```text
用户输入
-> LLM 输出 JSON 决策
-> Python 执行真实工具函数
-> observation 回传给 LLM
-> 循环若干轮
-> 输出稳定的前端 plan JSON
```

## 重要说明：是否真的调用 LLM

会调用，但前提是配置了真实 Provider 的环境变量。

默认 `LLM_PROVIDER=auto`：

- 如果有 `OPENAI_API_KEY`，自动走 OpenAI API。
- 如果有 `HUAWEI_MAAS_API_KEY`，自动走华为 MaaS API。
- 如果都没有，才走本地 `local fallback`，保证演示和测试不崩。

`run_agent_stream` 会先输出一个 `status` event，里面包含当前使用的 `provider` 和 `model`，例如：

```json
{"type":"status","provider":"openai","model":"gpt-4o-mini"}
```

## 目录

```text
agent/
├── __init__.py
├── harness.py          # Agent Harness：状态、工具顺序、重试、fallback、最终校验
├── planner.py          # Agent 主循环
├── llm_client.py       # OpenAI/Huawei/Local fallback Provider
├── prompts.py          # Agent 系统提示词
├── profile.py          # 用户画像分析
├── tools.py            # 真实 Python 工具
├── tool_registry.py    # 工具注册和执行上下文
└── schemas.py          # JSON 解析和输出兜底校验
```

## Harness 设计

当前 Agent 采用更接近业界常见的 guided ReAct harness：

```text
Harness 先整理关键上下文
-> get_current_time / decompose_goal / get_memory
-> 必要时 get_weather
-> analyze_user_profile / plan_time_slots
-> search_places / rank_places_for_plan / score_plans
-> LLM 基于 observation 继续决策预约、下单或最终答案
-> Harness 校验最终 steps 和 actions 必须来自真实地点库
```

这样做的目的不是替代 LLM，而是让 LLM 在更可靠的状态和工具结果上决策，减少漏查时间、漏用记忆、凭空编地点、预约动作和路线不一致等问题。

## LLM 配置

Agent 会自动读取项目根目录下的 `.env` 文件，也支持系统环境变量。系统环境变量优先级更高，`.env` 不会覆盖已经存在的环境变量。

OpenAI：

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=你的Key
OPENAI_MODEL=gpt-4o-mini
```

华为 MaaS：

```env
LLM_PROVIDER=huawei
HUAWEI_MAAS_API_KEY=你的Key
HUAWEI_MAAS_MODEL=deepseek-r1-250528
```

自动检测：

```env
LLM_PROVIDER=auto
```

本地 fallback：

```env
LLM_PROVIDER=local
```

## 本地测试

```bash
python test_agent_local.py
```

如果没有配置 API Key，测试会走 `local fallback`。如果想验证真实 LLM 调用，先设置对应 Key，再运行同一个测试脚本。

## main.py 接入方式

`main.py` 只负责登录鉴权、查询 `places` 和 `family_members`，不要写 Agent 核心逻辑。

```python
from agent.planner import run_agent_stream

@app.post("/v1/chat/send")
async def ai_chat_agent(payload: dict = Body(...), current_user: dict = Depends(get_current_user)):
    text = payload.get("text", "")

    # main.py 查询 places、family_members 后传给 Agent
    async def stream_generator():
        async for event in run_agent_stream(
            text=text,
            current_user=current_user,
            family_members=family_members,
            places=places,
        ):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(stream_generator(), media_type="text/event-stream")
```

## 上传服务器

需要上传：

```text
agent/
test_agent_local.py    # 可选，仅自测
README_AGENT.md        # 可选
```

服务器环境变量里配置 `LLM_PROVIDER` 和对应 API Key 后，Agent 就会由真实 LLM 决策工具调用。
