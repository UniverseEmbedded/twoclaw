# mem1与FreePool Router集成

本文档描述mem1记忆系统与FreePool Router的集成架构、职责边界和调用关系。

---

## 一、整体架构

### 1.1 组件定位

当前仓库的Python组件：

| 组件 | 位置 | 职责 |
|------|------|------|
| FreePool Router | `python/pool_router/` | LLM路由、账号池、降级、成本控制 |
| zeroclaw_tools | `python/zeroclaw_tools/` | langgraph工具循环执行器 |
| mem1 | `python/mem1/` | 记忆服务（新增） |

### 1.2 mem1的两种定位

**主定位**：独立memory service
- 提供`add/search/health`等记忆API
- 可独立部署、独立扩展

**可选定位**：OpenAI-compatible代理层
- 提供`/v1/chat/completions`
- 把记忆注入 + 再转发到FreePool Router

---

## 二、职责边界

### 2.1 mem1负责什么

- **记忆存储**：SQLite + 向量索引
- **记忆检索**：TagMemo算法（EPA、残差、boost、去重）
- **Embedding**：调用`gemini-embedding-001`
- **记忆管理**：增删改查、过期策略、去噪

### 2.2 FreePool Router负责什么

- **LLM路由**：GLM/Gemini等模型选择
- **账号池管理**：key池、cooldown、fallback
- **成本控制**：预算、配额、评分
- **请求转发**：统一的上游调用入口

### 2.3 关键原则

**mem1不要变成"第二个router"**

mem1不应该：
- 管理多个LLM账号
- 做模型选择逻辑
- 实现降级/重试策略

mem1只需要：
- 调用一个OpenAI-compatible endpoint
- 不关心背后是哪个模型

---

## 三、调用关系

### 3.1 推荐架构

```
┌─────────────────────────────────────────────────────────────┐
│                      ZeroClaw / OpenClaw                     │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                         mem1                                 │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐         │
│  │ /memories   │  │ /search     │  │ /v1/chat/*  │(可选)   │
│  └─────────────┘  └─────────────┘  └─────────────┘         │
└─────────────────────────────────────────────────────────────┘
         │                    │                    │
         │ embedding          │ LLM调用            │ 转发
         ▼                    ▼                    ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│ Gemini          │  │ FreePool Router │  │ FreePool Router │
│ (embedding)     │  │ /v1/chat/*      │  │ /v1/chat/*      │
└─────────────────┘  └─────────────────┘  └─────────────────┘
```

### 3.2 调用链路

**记忆写入**：
```
ZeroClaw → mem1.add() → Gemini(embedding) → SQLite
                      → Router(LLM标签生成) → SQLite
```

**记忆检索**：
```
ZeroClaw → mem1.search() → Gemini(embedding) → TagMemo算法 → SQLite/Index
```

**OpenAI代理模式**：
```
ZeroClaw → mem1./v1/chat/completions 
         → mem1.search() → 注入记忆
         → Router./v1/chat/completions → 返回结果
         → mem1.add() → 回写记忆
```

---

## 四、mem1内部需要LLM的场景

mem1内部需要调用LLM的场景，**全部走FreePool Router**：

| 场景 | 说明 | 调用方式 |
|------|------|----------|
| 标签生成 | 给chunk打标签 | `POST {ROUTER_BASE_URL}/v1/chat/completions` |
| 记忆抽取 | 从对话中抽取事实/偏好 | 同上 |
| Rerank | 向量检索后重排 | 同上 |
| Summary | 把topK记忆压缩 | 同上 |
| Hygiene | 记忆去噪/合并/过期判断 | 同上 |

### 4.1 配置方式

mem1配置：
```python
# mem1/config.py
FREEPOOL_BASE_URL = os.getenv("FREEPOOL_BASE_URL", "http://localhost:8000/v1")
```

mem1调用：
```python
# mem1/tagger_llm.py
async def generate_tags(text: str) -> list[str]:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{config.FREEPOOL_BASE_URL}/chat/completions",
            json={
                "model": "glm-4-flash",  # Router会处理实际路由
                "messages": [
                    {"role": "system", "content": TAG_PROMPT},
                    {"role": "user", "content": text}
                ]
            }
        )
    return parse_tags(response.json())
```

---

## 五、Embedding处理

### 5.1 为什么embedding不走Router

当前FreePool Router：
- 有`/v1/chat/completions`
- 有`/v1/models`
- **没有**`/v1/embeddings`

Rust侧有OpenAI-compatible embedding provider，会打`/v1/embeddings`。

### 5.2 推荐方案

**mem1直连Gemini做embedding**：

```python
# mem1/embedder_gemini.py
from google import genai

class GeminiEmbedder:
    def __init__(self):
        self.client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    
    async def embed_texts(self, texts: list[str]) -> np.ndarray:
        response = await self.client.models.embed_content(
            model="gemini-embedding-001",
            contents=texts
        )
        return np.array([e.values for e in response.embeddings], dtype=np.float32)
```

### 5.3 可选：让mem1提供embedding代理

如果想让Rust内置memory也吃到Gemini embedding，可以让mem1额外提供：

```
POST /v1/embeddings
```

用gemini-embedding-001生成向量后按OpenAI schema返回：

```json
{
  "object": "list",
  "data": [
    {
      "object": "embedding",
      "index": 0,
      "embedding": [0.1, 0.2, ...]
    }
  ],
  "model": "gemini-embedding-001",
  "usage": {
    "prompt_tokens": 10,
    "total_tokens": 10
  }
}
```

这样Rust侧embedding provider用`custom:http://mem1:port/v1`就能直接用。

---

## 六、部署架构

### 6.1 单机部署

```yaml
# docker-compose.yml
services:
  pool-router:
    build: ./python/pool_router
    ports:
      - "8000:8000"
    environment:
      - GLM_API_KEYS=...
      - GEMINI_API_KEY=...

  mem1:
    build: ./python/mem1
    ports:
      - "8001:8001"
    environment:
      - FREEPOOL_BASE_URL=http://pool-router:8000/v1
      - GEMINI_API_KEY=...
    volumes:
      - ./data/mem1:/data
```

### 6.2 端口约定

| 服务 | 端口 | 说明 |
|------|------|------|
| FreePool Router | 8000 | LLM路由 |
| mem1 | 8001 | 记忆服务 |

### 6.3 服务发现

mem1通过环境变量发现Router：
```
FREEPOOL_BASE_URL=http://pool-router:8000/v1
```

ZeroClaw通过环境变量发现mem1：
```
MEM1_BASE_URL=http://mem1:8001
```

---

## 七、容易踩的坑

### 7.1 不要把mem1变成"第二个router"

错误做法：
- mem1内部管理多个embedding provider
- mem1内部做模型选择
- mem1内部实现重试/降级

正确做法：
- embedding固定用gemini-embedding-001
- LLM调用全部走Router
- 只关注记忆策略与数据结构

### 7.2 embedding统一问题

当前状态：
- Router没有`/v1/embeddings`
- Rust侧有embedding provider

两种选择：
1. **mem1直连Gemini**（推荐，最简单）
2. 给Router加`/v1/embeddings`，统一管理

不要让mem1和Rust各自直连不同的embedding服务。

### 7.3 向量空间一致性

如果未来想让Rust内置memory和mem1共享向量索引：
- 必须用同一个embedding模型
- 向量维度必须一致
- 考虑让mem1提供统一的embedding代理

---

## 八、与ZeroClaw的接入方式

### 8.1 工具式接入（推荐起步）

ZeroClaw把"记忆"当工具调用：

```rust
// ZeroClaw侧
async fn memory_recall(query: &str, user_id: &str) -> Vec<Memory> {
    let response = http_client
        .post(&format!("{}/search", MEM1_BASE_URL))
        .json(&json!({
            "query": query,
            "user_id": user_id
        }))
        .send()
        .await?;
    response.json().await
}
```

### 8.2 OpenAI代理式接入

ZeroClaw把base_url指向mem1：

```rust
// ZeroClaw配置
let base_url = "http://mem1:8001/v1";  // 走mem1代理

// mem1自动处理：
// 1. search → 注入记忆
// 2. 转发到Router
// 3. add → 回写记忆
```

---

## 相关文档

- [VCP记忆系统概述](./claw_vcp_overview.md)
- [mem1 API规范](./claw_mem1_api_spec.md)
- [TagMemo算法迁移](./claw_tagmemo_algorithm.md)
- [mem1实现指南](./claw_mem1_implementation.md)
- [分阶段交付计划](./claw_mem1_phases.md)
