# 目录摄取工程方案

本文档描述如何实现"自动ingest本地文件夹、增量更新、可回放"的知识库摄取功能。

---

## 一、背景与目标

### 1.1 目标行为

服务启动后，自动扫描并持续监控指定目录（如`workspace/knowledge_base`），对新增/修改的文件：
- 分割成chunks
- 为每个chunk生成embedding
- 调Router生成tags
- 写入chunks/tags/chunk_tag_map
- 更新索引

### 1.2 核心需求

- 自动发现文件变更
- 增量更新（避免重复处理）
- 幂等写入（同一chunk不重复入库）
- 错误处理与降级
- 可观测性

---

## 二、组件边界

### 2.1 推荐架构

**kb_watcher（ZeroClaw侧）**：
- 监控目录、发现变更
- 文档解析（md/txt/pdf/docx）
- chunker（按段落/标题/长度）
- 调mem1 `/add`或`/mem1/batch_add`

**mem1（服务）**：
- 接收chunks
- 调embedding（gemini-embedding-001）
- 调Router做tags
- 写库 + 更新索引
- 提供`/search`给ZeroClaw读记忆

### 2.2 为什么不在mem1内部做watcher

- mem1保持"存储/检索服务"职责，不承担文件系统监控、解析各种格式的复杂性
- watcher失败不影响mem1服务可用性
- 更符合"mem1不做router"那种职责分离的思路
- 便于独立部署和扩展

---

## 三、支持文件类型与解析策略

### 3.1 第一阶段（MVP）

| 类型 | 解析方式 | 说明 |
|------|----------|------|
| `.md` | 纯文本解析 | 按标题/段落分割 |
| `.txt` | 纯文本解析 | 按段落分割 |

### 3.2 后续扩展

| 类型 | 解析方式 | 说明 |
|------|----------|------|
| `.pdf` | pdf解析库 | 提取文本后分割 |
| `.docx` | python-docx | 提取文本后分割 |
| `.html` | BeautifulSoup | 提取正文后分割 |

---

## 四、Chunking规范

### 4.1 切块策略

**按段落优先**：
- 段落内文本尽量保持完整
- 段落过长时按句子边界切分
- 保留标题作为chunk的上下文

**参数**：
- `chunk_size`: 最大chunk长度（默认500字符）
- `chunk_overlap`: chunk间重叠（默认50字符）
- `min_chunk_size`: 最小chunk长度（默认50字符）

### 4.2 chunk_id与chunk_hash生成规则

**chunk_id**：
```
{source_path_hash}:{chunk_index}
```

**chunk_hash**（幂等关键）：
```
hash(text + source_path + chunk_index)
```

**示例**：
```python
import hashlib

def generate_chunk_hash(text: str, source_path: str, chunk_index: int) -> str:
    content = f"{text}|{source_path}|{chunk_index}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]
```

---

## 五、增量更新与删除策略

### 5.1 文件级检测

**方式一：mtime + size（推荐，快速）**
- 检测文件的修改时间和大小
- 如果mtime或size变化，认为文件已更新

**方式二：文件hash（更稳但更慢）**
- 计算文件内容的hash
- 如果hash变化，认为文件已更新

### 5.2 chunk级检测

- 对比chunk_hash
- 如果chunk_hash未变化：跳过（不重新embedding/tagger）
- 如果chunk_hash变化：更新该chunk及其tag映射

### 5.3 文件删除处理

**策略一：软删除（推荐）**
- 标记chunk为inactive
- 保留历史记录
- 可恢复

**策略二：硬删除**
- 直接删除chunk及相关tag映射
- 不可恢复

### 5.4 幂等写入

- 写入前检查chunk_hash是否存在
- 存在则跳过或更新
- 不存在则插入

---

## 六、并发与吞吐

### 6.1 批量写入

使用`/mem1/batch_add`接口提升吞吐：
- 一次请求提交多个chunks
- 减少HTTP开销
- 支持dry-run验证

### 6.2 并发控制

- 最大并发embedding请求数（避免API限流）
- 最大并发tagger请求数
- 本地队列暂存待处理chunks

### 6.3 重试策略

| 场景 | 重试策略 |
|------|----------|
| embedding失败 | 指数退避，最多3次 |
| tagger失败 | 先写chunk，后补tags |
| mem1不可用 | 本地队列暂存，恢复后重放 |

---

## 七、错误处理与降级

### 7.1 错误分类

| 错误类型 | 处理方式 |
|----------|----------|
| 文件解析失败 | 跳过该文件，记录错误日志 |
| embedding失败 | 标记pending，后台重试 |
| tagger超时 | 先写chunk，后补tags |
| mem1不可用 | 本地队列暂存 |

### 7.2 降级策略

**写入降级**：
- tagger不可用 → 先写chunk，后补tags
- embedding不可用 → 标记pending，等待恢复

**检索降级**：
- tag_index不可用 → 回退到Phase A纯chunk检索
- mem1不可用 → ZeroClaw继续（无记忆模式）

---

## 八、索引维护

### 8.1 tag_index与chunk_index

| 索引 | 用途 | 更新时机 |
|------|------|----------|
| tag_index | tag向量检索 | tag新增/修改时upsert |
| chunk_index | chunk向量检索 | chunk新增/修改时upsert |

### 8.2 索引重建

**何时full rebuild**：
- embedding_model变更
- 索引损坏
- 手动触发

**何时增量upsert**：
- 新增chunk/tag
- 修改chunk/tag

### 8.3 重建接口

```
POST /mem1/reindex
{
  "scope_type": "user",
  "scope_id": "u1"
}
```

---

## 九、可观测性

### 9.1 日志字段

每次ingest操作记录：
- source_path
- chunk_count
- chunks_added
- chunks_skipped
- errors
- duration_ms

### 9.2 metrics

- ingest_total: 总ingest次数
- ingest_success: 成功次数
- ingest_failure: 失败次数
- chunks_added_total: 累计添加chunks
- embedding_latency_ms: embedding延迟
- tagger_latency_ms: tagger延迟

### 9.3 status接口

```
GET /mem1/status
{
  "last_ingest_at": "2026-02-26T10:00:00Z",
  "last_ingest_error": null,
  "tag_index_ready": true,
  "chunk_index_ready": true
}
```

---

## 十、配置示例

```yaml
# kb_watcher配置
watcher:
  watch_dir: "D:/path/to/knowledge_base"
  supported_extensions: [".md", ".txt"]
  poll_interval_ms: 5000
  
chunker:
  chunk_size: 500
  chunk_overlap: 50
  min_chunk_size: 50

concurrency:
  max_embedding_concurrent: 5
  max_tagger_concurrent: 3
  queue_size: 1000

retry:
  embedding_max_retries: 3
  embedding_backoff_ms: 1000
  tagger_timeout_ms: 30000
```

---

## 相关文档

- [mem1 API规范](./claw_mem1_api_spec.md)
- [mem1与FreePool Router集成](./claw_mem1_router_integration.md)
- [mem1实现指南](./claw_mem1_implementation.md)
- [分阶段交付计划](./claw_mem1_phases.md)
- [测试计划](./claw_mem1_test_plan.md)
