# mem1 API规范

本文档定义mem1记忆系统的对外API接口，分为两层：**mem0兼容层** + **mem1增强层**。

---

## 一、mem0 REST API兼容层

mem1提供与mem0 server一致的REST接口（同路径/同字段），以便：
- 直接换用mem0的客户端/工具
- 让上层像调用mem0一样调用mem1

### 1.1 POST /configure

配置记忆系统参数。

**请求体**：
```json
{
  "vector_store": {...},
  "llm": {...},
  "embedder": {
    "provider": "gemini",
    "model": "gemini-embedding-001"
  },
  "rag_params": {...}
}
```

**响应**：
```json
{"message": "Configuration set successfully"}
```

**实现说明**：
- 提取关心的字段（rag_params / embedder配置）
- 写入运行态配置
- 不必实现mem0配置里的每个后端（pg/neo4j等），但要"接受同样的payload"

---

### 1.2 POST /memories

写入记忆。

**请求体**：
```json
{
  "messages": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."}
  ],
  "user_id": "u1",
  "agent_id": "a1",
  "run_id": "r1",
  "metadata": {"k": "v"}
}
```

**行为**：切块 → 标签 → embedding → 入库 → 索引更新

**响应**：
```json
{
  "results": [
    {
      "id": "m_...",
      "memory": "...",
      "event": "ADD",
      "metadata": {...}
    }
  ]
}
```

---

### 1.3 GET /memories

获取记忆列表。

**查询参数**：
- `user_id`: 用户ID
- `agent_id`: 代理ID
- `run_id`: 运行ID
- `limit`: 返回数量限制
- `filters`: 过滤条件

**响应**：
```json
[
  {
    "id": "m_...",
    "memory": "...",
    "metadata": {...},
    "created_at": "..."
  }
]
```

---

### 1.4 GET /memories/{memory_id}

获取单条记忆。

**响应**：
```json
{
  "id": "m_...",
  "memory": "...",
  "metadata": {...},
  "created_at": "...",
  "updated_at": "..."
}
```

---

### 1.5 PUT /memories/{memory_id}

更新单条记忆。

**请求体**：
```json
{
  "memory": "更新后的内容",
  "metadata": {...}
}
```

**响应**：
```json
{
  "id": "m_...",
  "memory": "...",
  "event": "UPDATE",
  "metadata": {...}
}
```

---

### 1.6 DELETE /memories/{memory_id}

删除单条记忆。

**响应**：
```json
{"message": "Memory deleted successfully"}
```

---

### 1.7 DELETE /memories

批量删除记忆。

**查询参数**：
- `user_id`: 按用户删除
- `agent_id`: 按代理删除
- `run_id`: 按运行删除

**响应**：
```json
{"message": "Memories deleted successfully"}
```

---

### 1.8 POST /search

搜索记忆（核心接口）。

**请求体**：
```json
{
  "query": "我之前说过什么关于xxx？",
  "user_id": "u1",
  "agent_id": "a1",
  "run_id": "r1",
  "filters": {"diary": "2026-02-25"},
  "limit": 10
}
```

**行为**：VCP TagMemo检索（EPA → 残差金字塔 → TagMemo Boost → 去重）

**响应**：
```json
[
  {
    "id": "m_...",
    "memory": "...",
    "score": 0.83,
    "metadata": {...},
    "created_at": "..."
  }
]
```

---

### 1.9 POST /reset

重置记忆系统。

**请求体**：
```json
{
  "user_id": "u1",
  "agent_id": "a1",
  "run_id": "r1"
}
```

**行为**：按scope清空

**响应**：
```json
{"message": "Reset successful"}
```

---

### 1.10 GET /memories/{memory_id}/history

获取记忆的历史变更记录。

**响应**：
```json
[
  {
    "id": "h_...",
    "memory_id": "m_...",
    "event": "UPDATE",
    "old_memory": "...",
    "new_memory": "...",
    "created_at": "..."
  }
]
```

---

### 1.11 GET /

服务根路径，返回服务信息。

**响应**：
```json
{
  "status": "ok",
  "version": "1.0.0",
  "service": "mem1"
}
```

---

## 二、mem1增强API（工程化层）

这组接口不影响mem0兼容，但会极大提升调试效率。

### 2.1 GET /mem1/status

获取记忆系统状态。

**响应**：
```json
{
  "db_path": "/data/mem1/memories.db",
  "chunk_count": 12345,
  "tag_count": 2345,
  "index_ready": true,
  "embedder": "gemini-embedding-001",
  "rag_params_version": "sha1:abc123..."
}
```

---

### 2.2 GET /mem1/rag_params

获取当前rag参数。

**响应**：
```json
{
  "RAGDiaryPlugin": {
    "noise_penalty": 0.05,
    "tagWeightRange": [0.05, 0.45],
    "tagTruncationBase": 0.6,
    "tagTruncationRange": [0.5, 0.9]
  },
  "KnowledgeBaseManager": {
    "activationMultiplier": [0.5, 1.5],
    "dynamicBoostRange": [0.3, 2.0],
    "coreBoostRange": [1.20, 1.40],
    "deduplicationThreshold": 0.88,
    "techTagThreshold": 0.08,
    "normalTagThreshold": 0.015,
    "languageCompensator": {
      "penaltyUnknown": 0.05,
      "penaltyCrossDomain": 0.1
    }
  }
}
```

---

### 2.3 PUT /mem1/rag_params

热更新rag参数（无需重启）。

**请求体**：完整的rag_params JSON

**响应**：
```json
{
  "message": "rag_params updated",
  "version": "sha1:def456..."
}
```

---

### 2.4 POST /mem1/reindex

重建向量索引。

**请求体**（可选）：
```json
{
  "scope_type": "user",
  "scope_id": "u1"
}
```

**响应**：
```json
{
  "message": "Reindex completed",
  "chunks_indexed": 12345,
  "tags_indexed": 2345
}
```

---

### 2.5 POST /mem1/ingest

批量导入文本/文件。

**请求体**：
```json
{
  "source_type": "file",
  "source_path": "/path/to/diary",
  "user_id": "u1",
  "agent_id": "a1",
  "incremental": true
}
```

**响应**：
```json
{
  "message": "Ingest completed",
  "chunks_added": 123,
  "chunks_skipped": 45,
  "errors": []
}
```

---

## 三、OpenAI-compatible代理层

让`/v1/chat/completions`支持记忆自动注入与回写。

### 3.1 扩展请求体字段

在标准OpenAI请求体基础上，额外支持：

```json
{
  "model": "...",
  "messages": [...],
  "user_id": "u1",
  "agent_id": "a1",
  "run_id": "r1",
  "metadata": {...},
  "filters": {...},
  "limit": 10,
  "memory": {
    "enabled": true,
    "writeback": true,
    "max_context_chars": 4000
  }
}
```

**字段说明**：
- `user_id/agent_id/run_id`: 记忆作用域
- `metadata`: 附加元数据
- `filters`: 检索过滤条件
- `limit`: 检索数量
- `memory`: 记忆配置
  - `enabled`: 是否启用记忆
  - `writeback`: 是否回写记忆
  - `max_context_chars`: 最大注入字符数

### 3.2 记忆注入策略

当请求体携带`user_id/agent_id/run_id`任意一个时：

1. **构造query**：取最后一条user消息（或把最近N条user合并）作为搜索query
2. **mem1.search(query, scope, filters, limit)**：按VCP TagMemo算法检索topK chunks
3. **注入到messages**：在最前面插入一条system消息
   ```
   [MEMORIES]
   - (score=0.81, ts=...) ...
   - ...
   ```
4. **调用原来的Router路由**（不改现有选模逻辑）
5. **writeback**：把本轮user+assistant作为messages调`mem1.add(...)`

### 3.3 开关控制

可通过以下方式控制记忆行为：

**方式一：请求体配置**
```json
{
  "memory": {
    "enabled": true,
    "writeback": true,
    "max_context_chars": 4000
  }
}
```

**方式二：Header**
```
X-Memory-Mode: off|read|readwrite
```

**默认行为**：
- 如果传了user_id就启用readwrite
- 没传就完全不启用

---

## 四、错误码

| 状态码 | 含义 |
|--------|------|
| 200 | 成功 |
| 400 | 请求参数错误 |
| 404 | 资源不存在 |
| 500 | 服务器内部错误 |
| 503 | 服务不可用（如索引未就绪） |

**错误响应格式**：
```json
{
  "error": {
    "code": "INVALID_REQUEST",
    "message": "详细错误信息"
  }
}
```

---

## 相关文档

- [VCP记忆系统概述](./claw_vcp_overview.md)
- [TagMemo算法迁移](./claw_tagmemo_algorithm.md)
- [mem1与FreePool Router集成](./claw_mem1_router_integration.md)
- [mem1实现指南](./claw_mem1_implementation.md)
- [分阶段交付计划](./claw_mem1_phases.md)
