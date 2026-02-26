# 聊天时读写闭环

本文档描述ZeroClaw在对话过程中如何与mem1交互，实现记忆的自动读取和写入。

---

## 一、运行时调用流程

```
用户输入 → ZeroClaw
    │
    ├─→ mem1 /search（读取相关记忆）
    │       │
    │       └─→ 注入到prompt
    │
    ├─→ LLM生成回复
    │
    └─→ mem1 /add（写入门控后写入记忆）
```

---

## 二、Read Path（每轮用户输入怎么检索）

### 2.1 检索流程

1. **构造query**：取用户输入作为搜索query
2. **query embedding**：对query做embedding
3. **模型一致性检查**：query embedding模型与库内模型必须一致
4. **mem1 `/search`**：传入scope、topK、过滤条件
5. **注入prompt**：将命中内容注入ZeroClaw的对话链

### 2.2 检索参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| query | 用户输入 | - |
| user_id | 用户ID | - |
| agent_id | 代理ID | 可选 |
| limit | 返回数量 | 5 |
| filters | 过滤条件 | 可选 |

### 2.3 注入格式

将检索到的记忆注入到system prompt中：

```
[相关记忆]
1. (来源: diary/2026-02-25, 相关度: 0.85)
   用户之前提到项目代号是Moonlight...

2. (来源: conversation/2026-02-20, 相关度: 0.72)
   用户偏好使用Python进行开发...
```

**注入位置**：
- 在原有system prompt之后
- 在用户消息之前

### 2.4 模型一致性检查

**检查逻辑**：
```python
if query_embedding_model != db_embedding_model:
    log.warning("embedding model mismatch, boost disabled")
    return fallback_to_phase_a_search()
```

**处理方式**：
- 如果模型不一致，报警/拒绝boost
- 降级为纯文本检索（Phase A）

---

## 三、Write Path（每轮输出是否写回）

### 3.1 写入门控

**不是每轮对话都写，只写有价值的信息**

| 写入类型 | 条件 | 示例 |
|----------|------|------|
| 稳定事实 | 用户明确陈述的事实 | "我的项目代号是Moonlight" |
| 偏好 | 用户的长期偏好 | "我喜欢用Python" |
| 任务状态 | 正在进行的任务 | "我正在开发登录模块" |

**不写入的类型**：
- 情绪宣泄："烦死了"
- 临时推理："让我想想..."
- 无意义对话："好的"、"嗯"
- 重复信息：已存在的记忆

### 3.2 写入流程

1. **LLM判断**：判断本轮对话是否包含可写入的记忆
2. **结构化抽取**：LLM生成结构化记忆条目
3. **查重**：检查是否与已有记忆重复
4. **mem1 `/add`**：写入记忆

### 3.3 结构化记忆格式

```json
{
  "type": "fact",
  "subject": "用户",
  "predicate": "项目代号",
  "object": "Moonlight",
  "confidence": 0.9,
  "source": "conversation",
  "created_at": "2026-02-26T10:00:00Z"
}
```

**type类型**：
- `fact`: 事实
- `preference`: 偏好
- `task`: 任务状态
- `summary`: 摘要

---

## 四、scope映射规则

### 4.1 scope定义

| scope_type | 说明 | 隔离级别 |
|------------|------|----------|
| user | 用户级记忆 | 所有会话共享 |
| agent | 代理级记忆 | 特定代理共享 |
| run | 会话级记忆 | 单次会话有效 |

### 4.2 映射规则

| ZeroClaw字段 | mem1字段 |
|--------------|----------|
| user_id | scope_type=user, scope_id=user_id |
| agent_id | scope_type=agent, scope_id=agent_id |
| run_id | scope_type=run, scope_id=run_id |

### 4.3 检索优先级

检索时按以下优先级合并结果：
1. run级记忆（当前会话）
2. agent级记忆（当前代理）
3. user级记忆（当前用户）

---

## 五、失败与回退

### 5.1 mem1不可用

**处理方式**：ZeroClaw继续运行（无记忆模式）

```python
try:
    memories = await mem1.search(query, user_id)
except Mem1UnavailableError:
    memories = []
    log.warning("mem1 unavailable, running without memory")
```

### 5.2 Phase B不可用

**处理方式**：mem1回退到Phase A搜索

```python
if not tag_index_ready or not tagger_available:
    return phase_a_search(query_vector)
```

### 5.3 embedding模型不一致

**处理方式**：报警/拒绝boost，降级为纯文本检索

```python
if query_model != db_model:
    log.warning("embedding model mismatch")
    return phase_a_search(query_vector)
```

---

## 六、配置示例

```yaml
memory:
  enabled: true
  read:
    limit: 5
    min_score: 0.5
    inject_format: "list"  # list / summary
  write:
    enabled: true
    types: [fact, preference, task]
    min_confidence: 0.7
    dedup_threshold: 0.9
  fallback:
    on_mem1_unavailable: "continue"  # continue / abort
    on_model_mismatch: "phase_a"     # phase_a / abort
```

---

## 七、监控指标

| 指标 | 说明 |
|------|------|
| memory_read_total | 总读取次数 |
| memory_read_success | 成功读取次数 |
| memory_read_latency_ms | 读取延迟 |
| memory_write_total | 总写入次数 |
| memory_write_filtered | 被过滤的写入次数 |
| memory_fallback_total | 回退次数 |

---

## 相关文档

- [mem1 API规范](./claw_mem1_api_spec.md)
- [mem1与FreePool Router集成](./claw_mem1_router_integration.md)
- [写入策略](./claw_mem1_write_policy.md)
- [目录摄取工程方案](./claw_mem1_ingestion.md)
- [分阶段交付计划](./claw_mem1_phases.md)
