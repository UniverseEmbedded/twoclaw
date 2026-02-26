# 写入策略

本文档定义mem1记忆系统的写入策略，包括什么时候写、写什么、怎么避免污染。

---

## 一、写入分类

### 1.1 文档型记忆

**来源**：由kb_watcher从文件摄取

**特点**：
- 有明确的source_path
- 可增量更新（基于mtime/hash）
- 可删除（文件删除时同步删除）

**写入时机**：
- 文件新增/修改时
- 手动触发ingest时

### 1.2 对话型记忆

**来源**：由ZeroClaw对话产生

**特点**：
- 无固定source_path
- 需要写入门控
- 需要去重和过期管理

**写入时机**：
- 对话轮次结束时（经门控判断）

---

## 二、对话型写入门控

### 2.1 写入判据

**应该写入的情况**：

| 类型 | 判据 | 示例 |
|------|------|------|
| 稳定事实 | 用户明确陈述的事实，非临时性 | "我的名字叫Alice"、"项目代号是Moonlight" |
| 偏好 | 用户的长期偏好或习惯 | "我喜欢用Python"、"我不喜欢早起" |
| 任务状态 | 正在进行的任务或计划 | "我正在开发登录模块"、"下周要交报告" |
| 关系 | 与他人的关系信息 | "我的导师是Bob" |

**不应写入的情况**：

| 类型 | 判据 | 示例 |
|------|------|------|
| 情绪宣泄 | 临时情绪表达 | "烦死了"、"好开心" |
| 临时推理 | 思考过程中的中间结论 | "让我想想..."、"可能是..." |
| 无意义对话 | 无信息量的回复 | "好的"、"嗯"、"知道了" |
| 重复信息 | 与已有记忆高度相似 | 已记录过的事实再次提及 |
| 敏感信息 | 涉及隐私或安全 | 密码、密钥、个人隐私 |

### 2.2 LLM判断Prompt

```
你是一个记忆判断助手。分析以下对话，判断是否应该写入长期记忆。

用户消息：{user_message}
助手回复：{assistant_message}

判断标准：
1. 只写入稳定事实、偏好、任务状态、关系信息
2. 不写入情绪宣泄、临时推理、无意义对话
3. 不写入敏感信息

输出JSON格式：
{
  "should_write": true/false,
  "type": "fact/preference/task/relation/none",
  "content": "提取的记忆内容",
  "confidence": 0.0-1.0
}
```

### 2.3 查重策略

**方式一：hash查重**
- 对记忆内容做hash
- 如果hash已存在，跳过

**方式二：语义查重**
- 对新记忆做embedding
- 与已有记忆做相似度检索
- 如果相似度超过阈值（如0.9），跳过或合并

---

## 三、结构化记忆格式

### 3.1 JSON Schema

```json
{
  "type": "fact",
  "subject": "用户",
  "predicate": "项目代号",
  "object": "Moonlight",
  "confidence": 0.9,
  "source": "conversation",
  "scope_type": "user",
  "scope_id": "u1",
  "created_at": "2026-02-26T10:00:00Z",
  "ttl": null
}
```

### 3.2 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| type | string | 是 | fact/preference/task/relation/summary |
| subject | string | 是 | 主语（通常是"用户"） |
| predicate | string | 是 | 谓语/属性名 |
| object | string | 是 | 宾语/属性值 |
| confidence | float | 是 | 置信度（0-1） |
| source | string | 是 | 来源：conversation/document |
| scope_type | string | 是 | user/agent/run |
| scope_id | string | 是 | 作用域ID |
| created_at | string | 是 | 创建时间（ISO 8601） |
| ttl | int | 否 | 生存时间（秒），null表示永久 |

### 3.3 type类型详解

| type | 说明 | 示例 |
|------|------|------|
| fact | 客观事实 | 项目代号=Moonlight |
| preference | 用户偏好 | 喜欢用Python |
| task | 任务状态 | 正在开发登录模块 |
| relation | 人际关系 | 导师=Bob |
| summary | 对话摘要 | 用户今天讨论了X、Y、Z |

---

## 四、更新策略

### 4.1 add vs update

**add**：追加新记忆
- 适用于：新信息、独立事件
- 不影响已有记忆

**update**：更新已有记忆
- 适用于：同一属性的新值、信息修正
- 需要找到对应的已有记忆

### 4.2 更新判据

当新记忆与已有记忆满足以下条件时，执行update：
1. 相同的subject和predicate
2. object不同
3. 时间更新

**示例**：
- 已有：项目代号=Moonlight
- 新增：项目代号=Starlight
- 结果：项目代号=Starlight（更新）

### 4.3 历史记录

每次update保留历史：
```
memory_history:
  - event: UPDATE
    old_value: Moonlight
    new_value: Starlight
    created_at: 2026-02-26T10:00:00Z
```

---

## 五、过期与清理

### 5.1 TTL机制

| type | 默认TTL | 说明 |
|------|---------|------|
| fact | null（永久） | 事实通常长期有效 |
| preference | null（永久） | 偏好通常长期有效 |
| task | 7天 | 任务状态有时效性 |
| relation | null（永久） | 关系通常长期有效 |
| summary | 30天 | 摘要有时效性 |

### 5.2 清理策略

**定期清理**：
- 每天扫描过期记忆
- 删除或标记inactive

**手动清理**：
- 用户主动删除
- 管理员批量清理

---

## 六、安全与隐私

### 6.1 敏感信息过滤

**过滤规则**：
- 不记录密码、密钥、token
- 不记录身份证号、银行卡号
- 不记录私密对话内容

**实现方式**：
- 正则匹配敏感模式
- LLM判断敏感内容

### 6.2 scope隔离

| scope_type | 隔离级别 |
|------------|----------|
| user | 用户间完全隔离 |
| agent | 代理间完全隔离 |
| run | 会话间完全隔离 |

**查询时**：
- 只返回当前scope的记忆
- 不泄露其他scope的信息

---

## 七、配置示例

```yaml
write_policy:
  enabled: true
  
  gate:
    min_confidence: 0.7
    dedup_threshold: 0.9
    filter_sensitive: true
  
  types:
    fact:
      enabled: true
      ttl: null
    preference:
      enabled: true
      ttl: null
    task:
      enabled: true
      ttl: 604800  # 7天
    relation:
      enabled: true
      ttl: null
    summary:
      enabled: false
      ttl: 2592000  # 30天
  
  update:
    enabled: true
    keep_history: true
  
  cleanup:
    enabled: true
    schedule: "0 3 * * *"  # 每天凌晨3点
```

---

## 相关文档

- [聊天时读写闭环](./claw_mem1_zeroclaw_runtime.md)
- [mem1 API规范](./claw_mem1_api_spec.md)
- [目录摄取工程方案](./claw_mem1_ingestion.md)
- [分阶段交付计划](./claw_mem1_phases.md)
