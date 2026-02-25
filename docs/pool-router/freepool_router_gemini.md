# FreePool Router - Gemini 集成

## 概述

将 Google Gemini API 接入 FreePool Router，利用 $300 赠金作为 CreditPool，与 GLM 免费模型池（FreePool）形成混合调度。

---

## 核心问题

1. **Gemini 能不能通过 API 获取可用模型列表？**
2. **能不能拿到赠金/花费（至少成本估算）？**
3. **Gemini（付费/赠金）和 GLM（免费模型池）混合后，调度上怎么处理？**
4. **`google.genai` 这条路是否适合访问新模型（比如 preview）？**

---

## 结论速览

### 1) 可以通过 API / SDK 获取模型列表

Google 的 Gemini Models API 明确有 `models` endpoint，可程序化列出模型与元数据（功能、上下文窗口等）。

`google-genai` Python SDK 也明确支持 `client.models.list()`（同步/异步都有），还能分页。

### 2) "赠金剩余金额 / 实际消耗金额"一般不能靠 Gemini 推理 API 直接返回

通常能拿到的是：

- **token 使用量 / usage metadata**（请求级）
- **模型列表与能力信息**
- 但**不是直接"剩余赠金 $X"** 这种字段（那是 Billing 侧）

Google Cloud Free Trial 文档确认：

- 新用户通常有 **$300 Welcome credit**
- 有效期 **90 天**
- 可用于覆盖符合范围的服务（包括 AI APIs / Vertex AI 能力）

但"剩余赠金多少"一般要去：

- **Cloud Billing 控制台**
- 或结合 **Cloud Billing / Budgets / Billing Export（BigQuery）** 等方式做监控

### 3) 可以做"请求级成本估算"，并纳入 FreePool Router

这是最该做的。因为 Gemini 的真实账单是 Billing 延迟结算，而 Router 需要**实时决策**。

### 4) `google.genai` 是合适路线（尤其要追新模型 / preview）

Google 官方 Vertex AI 文档明确推荐使用 **Google Gen AI SDK**（`google-genai`），并给出 Vertex AI 和 Vertex Express Mode 的示例。

---

## google.genai 原型脚本

```python
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional
import os
import json

os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = r'D:\2024-12-23\used-exe-no-ui\litellm\gen-lang-client-xxxx.json' 

from google import genai
from google.genai import types

app = FastAPI(title="Gemini Proxy")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

client = genai.Client(
    vertexai=True,
    api_key='AQ.XXXXX',
)

MODEL_MAPPING = {
    "gemini-3-flash-preview": "gemini-3-flash-preview",
    "gemini-2.5-flash": "gemini-2.5-flash",
}

class Message(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    model: str
    messages: List[Message]
    max_tokens: Optional[int] = 4000
    temperature: Optional[float] = 1.0
    top_p: Optional[float] = 0.95
    stream: Optional[bool] = False

def convert_messages(messages: List[Message]) -> List[types.Content]:
    contents = []
    system_content = None
    
    for msg in messages:
        if msg.role == "system":
            if system_content is None:
                system_content = msg.content
            else:
                system_content += "\n" + msg.content
        else:
            contents.append(
                types.Content(
                    role=msg.role,
                    parts=[types.Part(text=msg.content)]
                )
            )
    
    if system_content and contents:
        first_content = contents[0]
        if first_content.role == "user":
            system_prefix = f"[系统指令]\n{system_content}\n\n[用户消息]\n"
            first_content.parts[0].text = system_prefix + first_content.parts[0].text
    
    return contents

@app.post("/v1/chat/completions")
async def chat_completions(request: ChatRequest):
    try:
        model_name = MODEL_MAPPING.get(request.model, request.model)
        contents = convert_messages(request.messages)
        
        config = types.GenerateContentConfig(
            temperature=request.temperature,
            top_p=request.top_p,
            max_output_tokens=request.max_tokens,
        )
        
        response = client.models.generate_content(
            model=model_name,
            contents=contents,
            config=config,
        )
        
        return {
            "id": "chatcmpl-gemini-proxy",
            "object": "chat.completion",
            "created": 0,
            "model": request.model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": response.text
                    },
                    "finish_reason": "stop"
                }
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/v1/models")
async def list_models():
    return {
        "models": [
            {
                "name": "models/gemini-3-flash-preview",
                "version": "001",
                "displayName": "Gemini 3 Flash Preview",
                "description": "Gemini 3 Flash Preview model with high performance",
                "inputTokenLimit": 1048576,
                "outputTokenLimit": 8192,
                "supportedGenerationMethods": ["generateContent", "countTokens", "streamGenerateContent"],
                "temperature": 1.0,
                "topP": 0.95,
                "topK": 64
            },
            {
                "name": "models/gemini-2.5-flash",
                "version": "001",
                "displayName": "Gemini 2.5 Flash",
                "description": "Gemini 2.5 Flash model",
                "inputTokenLimit": 1048576,
                "outputTokenLimit": 8192,
                "supportedGenerationMethods": ["generateContent", "countTokens", "streamGenerateContent"],
                "temperature": 1.0,
                "topP": 0.95,
                "topK": 64
            }
        ]
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=4000)
```

---

## 原型脚本改进建议

### A. `/v1/models` 不要手写静态列表，改成真实拉取

建议改成：

- 启动时缓存 `client.models.list()`
- 定时刷新（比如 5~30 分钟）
- 过滤只保留允许的模型（`gemini-*flash*`, `gemini-*pro*`, imagen/veo 等）
- 返回带能力标签（模态、context、方法支持）

这样能避免：

- preview 模型变更时手动改代码
- 模型下线/改名导致路由错误

### B. 把 `usage_metadata` 真正透传出来

`google-genai` 的 `GenerateContentResponse` 有 `usage_metadata` 字段。

建议在 proxy 层做两件事：

1. 从 `response.usage_metadata` 提取 token 统计
2. 写入路由日志（endpoint、模型、latency、tokens、是否失败）

这会直接喂给 FreePool Router 做：

- 实时成本估算
- 模型效率评分
- 预算保护

### C. 在路由前先调用 `count_tokens` / `compute_tokens`

`google-genai` 支持：

- `client.models.count_tokens(...)`
- Vertex AI 上还支持 `compute_tokens(...)`（仅 Vertex AI 支持）

这对"难度定义 + 模型路由"非常有价值：

- 在真正生成前先估算 prompt token
- 判断是否超长（需要 Gemini 长上下文 / GLM 4.7）
- 估算成本级别（CreditPool 是否值得用）

---

## 赠金消耗 / 金额消耗的现实做法

### 层 1：请求级"实时成本估算"（Router 内部）

用途：实时路由决策

做法：

- 用请求/响应 tokens × 配置的单价表（按模型/区域）
- 算一个 `estimated_cost_usd`
- 写入本地 DB（SQLite/Postgres）

这层是**实时且可控**，用于调度。

> Vertex AI 定价页面明确按模型与 token/input-output 计费，并且说明只对返回 200 的请求计费。

### 层 2：账单级"真实成本跟踪"（Google Cloud Billing）

用途：对账、预算、赠金保护

做法（从易到难）：

1. **Billing 控制台 + Budget 告警**（最简单）
2. **Cloud Billing Budgets API**（程序化预算与阈值告警）
3. **Cloud Billing Export to BigQuery**（最强，延迟但可分析）

### `credit_balance_mode` 设计

- `manual`：手工输入剩余赠金（最开始）
- `estimated`：用本地估算余额（基于初始 $300 - 估算累计）
- `billing_synced`：接入 Billing 数据做校准（中后期）

这样即使一开始没有账单 API 集成，也能先跑起来。

---

## Gemini + GLM 混合调度策略

加入 Gemini 以后，Router 不再只是"能力/并发调度"，还变成了"成本策略调度"。

### 新增维度：Budget Policy（预算策略）

在之前的评分函数上加一层门控：

#### 先决策资源池，再选模型

1. **先选 Pool**
   - `free_only`
   - `prefer_free_then_credit`
   - `credit_allowed`
   - `premium_only`（高价值任务）
2. **再在 pool 内做能力/难度/拥塞路由**

#### 例子

- 普通群聊水聊：`free_only`
- 普通看图问答：`prefer_free_then_credit`
- 长上下文复杂任务：`credit_allowed`
- 重要 coding agent / 生产任务：`credit_allowed` 或 `premium_only`

### 请求 hint 扩展

- `x-freepool-budget-policy: free_only|prefer_free_then_credit|credit_allowed|premium_only`
- `x-freepool-max-estimated-cost-usd: 0.02`
- `x-freepool-value-tier: low|normal|high`
- `x-freepool-lane: text_fast|reasoning|vision|image_gen|video_gen`

这样 ZeroClaw 改动仍然很少，但 Router 能做很高级的策略。

---

## GeminiProviderAdapter 设计

Router 启动时 / 定时执行：

1. `client.models.list()`
2. 过滤可用模型
3. 读取 metadata（支持的方法、token limit 等）
4. 写入 Router 的 model registry

### 为什么这很重要

preview 模型（例如 `gemini-3-flash-preview`）可能：

- 名字变
- 权限开通方式变
- 可用性按项目/区域不同

动态 list 比手写 mapping 稳定太多。

---

## 原型脚本踩坑点

### 1) `vertexai=True` + `api_key` + `GOOGLE_APPLICATION_CREDENTIALS` 混用

从官方示例看：

- `vertexai=True, api_key=...` 是 Vertex AI express mode 的一种用法
- 也有通过环境变量 `GOOGLE_CLOUD_PROJECT / LOCATION / GOOGLE_GENAI_USE_VERTEXAI=True` 等方式创建 client 的模式

建议明确拆开两种模式：

- **Vertex express mode（API key）**
- **Vertex standard（ADC / service account）**

做成配置项，不要混用隐式行为。

### 2) 流式接口的同步/异步问题

`client.models.generate_content_stream(...)` 在同步迭代风格下，在 FastAPI `StreamingResponse` 下高并发时可能阻塞事件循环。

建议：

- 用 SDK 的异步接口（`client.aio...`）或
- 放到线程池

---

## 最小实现路线

### Phase 1：先做 Gemini Adapter（不碰 ZeroClaw）

- `list_models()` → 动态模型注册
- `count_tokens()` → 路由前估 token
- `generate_content()` / `stream_generate_content()` → 正常推理
- 记录 `usage_metadata` → 本地成本估算

### Phase 2：加预算策略

- `estimated_credit_remaining_usd`（手工初始化，Router 自己扣减）
- `daily_cap_usd` / `hourly_cap_usd`
- `budget_policy`（free_only / prefer_free_then_credit / ...）

### Phase 3：接 Billing 监控（校准）

- Budget 阈值告警
- Billing Export（BigQuery）对账
- 修正估算误差

---

## 模型池分类建议

### A. `FreeGuaranteed`（GLM 免费）

- 默认首选
- 高并发任务主力
- 成本视为 0（但有并发和质量成本）

### B. `CreditMetered`（Gemini/Vertex，吃赠金）

- 有实时成本估算
- 有预算门控
- 高价值/高难任务兜底

### C. `PremiumHardCap`（未来可能接别的付费模型）

- 强限制
- 只在明确授权时用

这样 Router 就会很像真正的生产调度系统，而不是"几个 API key 轮询器"。

---

## 参考链接

- [Gemini Models API](https://ai.google.dev/api/models) - Models endpoint 文档
- [Google Gen AI SDK](https://googleapis.github.io/python-genai/) - Python SDK 文档
- [Google Cloud Free Trial](https://docs.cloud.google.com/free/docs/free-cloud-features) - $300 Welcome credit 说明
- [Cloud Billing Budgets](https://docs.cloud.google.com/billing/docs/how-to/budgets) - 预算与告警
- [Vertex AI SDK Overview](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/sdks/overview) - Gen AI SDK 使用指南
- [Vertex AI Pricing](https://cloud.google.com/vertex-ai/generative-ai/pricing) - 定价说明

---

## 多客户端池实现

### 核心结论

`google.genai` **不支持"一个 Client 同时绑定多个 key / 多个 ADC"**，但支持自己做"多 Client 池"。

每个 `genai.Client(...)` 的构造参数是单个 `api_key`、单个 `credentials`，不是列表。

### 实现方式

#### A. Gemini Developer API（多 API Key）

每个 key 建一个 `genai.Client`，放进池子里轮询使用：

```python
from google import genai

gemini_dev_clients = [
    genai.Client(api_key=k) for k in GEMINI_API_KEYS
]
```

#### B. Vertex AI（多 ADC / 多服务账号）

不要靠切 `GOOGLE_APPLICATION_CREDENTIALS`（全局变量，FastAPI 并发下容易串），而是：

```python
from google import genai
from google.oauth2 import service_account

vertex_clients = []
for sa in SERVICE_ACCOUNT_FILES:
    creds = service_account.Credentials.from_service_account_file(sa)
    vertex_clients.append(
        genai.Client(
            vertexai=True,
            credentials=creds,
            project=PROJECT_ID,
            location="us-central1",
        )
    )
```

### Gemini 的特殊点

Gemini 可以多 key/多 client，但很多限额是 **project 级共享**（不是 key 级）。

所以 Gemini 不能简单理解成"加 key 就线性扩容"，Router 要按 project 维度做限额控制。

### 统一抽象：QuotaBucket

每个 endpoint 归属一个 bucket：
- GLM：bucket = `glm_account_x`
- Gemini：bucket = `gemini_project_y`

Router 不需要一开始纠结"它到底是 key 限额还是 project 限额"，只要知道：
- 这个 endpoint 属于哪个共享资源桶
- 这个桶当前容量如何（并发/冷却/限流状态）

---

## 认证模式建议

### 不要混用

原型代码里同时用了：
- `os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = ...`
- `genai.Client(vertexai=True, api_key=...)`

这会让认证路径变得不透明。建议拆成两种明确模式：

#### Vertex Express 模式
```python
client = genai.Client(vertexai=True, api_key=...)
```

#### Vertex Standard/ADC 模式
```python
# 环境变量方式
os.environ['GOOGLE_CLOUD_PROJECT'] = '...'
os.environ['GOOGLE_GENAI_USE_VERTEXAI'] = 'True'

# 或显式凭据
from google.oauth2 import service_account
creds = service_account.Credentials.from_service_account_file(sa)
client = genai.Client(vertexai=True, credentials=creds, project=..., location=...)
```

### 不建议的做法

1. **请求中途改 `os.environ["GOOGLE_APPLICATION_CREDENTIALS"]`**：单进程 FastAPI 下会有并发问题
2. **一个 client 混用 `api_key` + `GOOGLE_APPLICATION_CREDENTIALS`**：可读性差、行为不稳定

---

## 流式接口注意事项

`client.models.generate_content_stream(...)` 在同步迭代风格下，在 FastAPI `StreamingResponse` 下高并发时可能阻塞事件循环。

建议：
1. 用 SDK 的异步接口（`client.aio...`）
2. 或把同步流式调用扔线程池（`anyio.to_thread.run_sync`）

---

## 测试策略

### dry-run 模式

Router 增加开关 `FREEPOOL_DRY_RUN=1`，开启后：
- 不调用任何真实模型
- 只返回"本来会选哪个池/哪个模型/哪个账号"的结果

返回内容示例：
```json
{
  "selected_pool": "glm_free",
  "selected_model": "glm-4-flash",
  "selected_account": "key_2",
  "difficulty_initial": 22,
  "difficulty_final": 68,
  "reclassified_by": "glm4flash_stub",
  "reason": "low_difficulty_recheck"
}
```

### 可插拔分类器

把"调用 GLM4Flash"抽象成接口：

```python
class DifficultyClassifier:
    def classify(self, req) -> dict:  # {lane, difficulty}
        pass

class RealDifficultyClassifier(DifficultyClassifier):
    # 真调 GLM4Flash（上线用）
    pass

class StubDifficultyClassifier(DifficultyClassifier):
    # 不调模型，按固定规则返回（测试用）
    pass
```

### Mock Provider

```python
class MockProviderAdapter:
    def __init__(self, config):
        # gemini-2.5-pro: always_success / always_fail / rate_limit / slow
        # veo-3: disabled
        # glm-4.7-flash: success with high latency
        # glm-4-flash: success fast
        pass
```

### 开发期模式开关

| 开关 | 说明 |
|------|------|
| `FREEPOOL_DRY_RUN=1` | 不实际调用模型，只返回路由决策 |
| `FREEPOOL_CLASSIFIER_MODE=stub\|real` | 低难复核用假分类器还是真 GLM4Flash |
| `FREEPOOL_PROVIDER_MODE=mock\|real` | Provider adapter 用 mock 还是真实 API |

组合使用：
- `dry-run + stub + mock`：纯逻辑测试（0成本）
- `real-run + stub + mock`：测 Router 执行流程（0成本）
- `real-run + real-classifier + mock`：只测低难复核真实效果（低成本）
- `real-run + real + real`：上线前验证
