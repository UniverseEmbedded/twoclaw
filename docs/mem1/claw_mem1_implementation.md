# mem1实现指南

本文档描述mem1记忆系统的具体实现细节，包括语言选择、目录结构、打包配置和代码改造清单。

---

## 一、实现语言选择

### 1.1 首选：Python

**原因**：
- 记忆系统需要：写入/检索/重排/去重/评估/可观测性
- Python生态（FastAPI、pydantic、SQLite、向量库客户端、RAG工具链）最快做出"可用闭环"
- Gemini embedding的调用/封装在Python里最省事
- 后面会反复调参、做A/B、写评估脚本、回放日志——Python效率碾压

**适合当前阶段**：
- 正从"参考材料vcp-mem1"抽象出一套可运行组件
- 这一步最重要的是**接口、数据结构、写入策略、可观测**，而不是极致性能

### 1.2 次选：TypeScript

**原因**：
- 如果整体运行环境/生态已经偏Node（跟OneBot/NapCat、WebSocket、插件系统更紧密耦合）
- 更容易做成"独立npm包 + server"，对前端/插件/生态扩展友好

**注意**：
- 真正做向量检索、批量embedding、复杂去重/线代（VCP的EPA/残差/SVD），TS会更快碰到"科学计算不爽/性能不稳/实现成本更高"的点

### 1.3 不建议一开始就用：Rust

**原因**：
- Rust最适合在确定了接口和算法后，把热点（向量计算、索引、去重）做成库或wasm/ffi加速
- 但现在还在"需求/形态"快速迭代期，Rust会显著放慢试错速度

### 1.4 推荐方案

**方案A：Python服务 +（可选）Rust热点加速**
- 先用Python/FastAPI做出`add/search/health` +（可选）`/v1/chat/completions`代理层
- embedding provider：`gemini-embedding-001`
- store：先SQLite + 简单向量索引（或直接Qdrant）跑通
- 等"接入ZeroClaw体验稳定"后，再把EPA/去重/索引热点迁到Rust（做成一个小crate，Python用pyo3调）

**一句话决策**：
- 要最快出可用闭环 → Python
- 要跟现有Node/OneBot/插件生态强绑定 → TS
- 要做一个"内核级、长期维护、极致性能"的记忆引擎 → Rust（但别当第一步）

---

## 二、目录结构

### 2.1 推荐结构

```
python/mem1/
├── __init__.py
├── __main__.py           # 入口：uvicorn启动
├── server.py             # FastAPI app
├── config.py             # 配置管理
├── default_rag_params.json
│
├── api/
│   ├── __init__.py
│   ├── mem0_compat.py    # mem0兼容API
│   ├── mem1_enhanced.py  # mem1增强API
│   └── openai_proxy.py   # OpenAI代理层（可选）
│
├── store/
│   ├── __init__.py
│   ├── sqlite.py         # SQLite存储
│   └── models.py         # 数据模型
│
├── index/
│   ├── __init__.py
│   └── usearch.py        # USearch向量索引
│
├── embedder/
│   ├── __init__.py
│   └── gemini.py         # Gemini embedding
│
├── pipelines/
│   ├── __init__.py
│   ├── add.py            # 写入流程
│   ├── search.py         # 检索流程
│   └── ingest.py         # 批量导入
│
├── algorithms/
│   ├── __init__.py
│   ├── chunker.py        # 文本切块
│   ├── tagger.py         # 标签生成
│   ├── epa.py            # EPA分析
│   ├── residual.py       # 残差金字塔
│   ├── tagmemo_boost.py  # TagMemo Boost
│   └── dedup.py          # 结果去重
│
└── utils/
    ├── __init__.py
    └── helpers.py
```

### 2.2 关键文件说明

| 文件 | 职责 |
|------|------|
| `server.py` | FastAPI应用、路由挂载、中间件 |
| `config.py` | 环境变量读取、rag_params加载 |
| `api/mem0_compat.py` | 实现`/configure`、`/memories`、`/search`等mem0兼容接口 |
| `api/mem1_enhanced.py` | 实现`/mem1/status`、`/mem1/rag_params`等增强接口 |
| `api/openai_proxy.py` | 实现`/v1/chat/completions`代理层 |
| `store/sqlite.py` | SQLite表结构、CRUD操作 |
| `index/usearch.py` | USearch索引封装（chunk_index、tag_index） |
| `embedder/gemini.py` | Gemini embedding调用封装 |
| `pipelines/add.py` | 写入流程编排：切块→标签→embedding→入库 |
| `pipelines/search.py` | 检索流程编排：EPA→残差→boost→检索→去重 |
| `algorithms/*` | 各算法组件的Python实现 |

---

## 三、打包与运行配置

### 3.1 pyproject.toml修改

在现有`python/pyproject.toml`基础上：

```toml
[project]
name = "zeroclaw"
# ...

[project.scripts]
pool-router = "pool_router.__main__:main"
mem1 = "mem1.__main__:main"  # 新增

[tool.hatch.build.targets.wheel]
packages = ["zeroclaw_tools", "pool_router", "mem1"]  # 新增mem1

[project.dependencies]
# 现有依赖...
# 新增依赖
numpy = ">=1.24"
usearch = ">=2.0"
google-genai = ">=0.3"
aiosqlite = ">=0.19"
orjson = ">=3.9"  # 可选，性能优化
```

### 3.2 启动入口

```python
# mem1/__main__.py
import uvicorn
from mem1.config import settings

def main():
    uvicorn.run(
        "mem1.server:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug
    )

if __name__ == "__main__":
    main()
```

### 3.3 配置管理

```python
# mem1/config.py
from pydantic_settings import BaseSettings
from pathlib import Path

class Settings(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8001
    debug: bool = False
    
    db_path: Path = Path("/data/mem1/memories.db")
    index_path: Path = Path("/data/mem1/index")
    
    freepool_base_url: str = "http://localhost:8000/v1"
    gemini_api_key: str = ""
    
    rag_params_path: Path = Path(__file__).parent / "default_rag_params.json"
    
    class Config:
        env_prefix = "MEM1_"

settings = Settings()
```

---

## 四、具体文件改造清单

### 4.1 新增文件

| 文件 | 说明 |
|------|------|
| `python/mem1/` | 整个目录 |
| `python/mem1/default_rag_params.json` | 从`vcp-mem1/rag_params.json`复制 |

### 4.2 修改文件

#### `python/pool_router/server.py`

新增路由并挂载mem1：

```python
# 新增导入
from mem1.api import mem0_compat, mem1_enhanced

# 新增路由
app.include_router(mem0_compat.router, tags=["mem0-compat"])
app.include_router(mem1_enhanced.router, tags=["mem1-enhanced"])

# 修改现有的 /v1/chat/completions handler
@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    # 解析并剥离mem0字段
    user_id = request.pop("user_id", None)
    agent_id = request.pop("agent_id", None)
    run_id = request.pop("run_id", None)
    memory_config = request.pop("memory", {})
    
    if user_id and memory_config.get("enabled", True):
        # 调mem1.service.search()
        # 注入到messages
        pass
    
    # 原有路由逻辑
    response = await router.route(request)
    
    if user_id and memory_config.get("writeback", True):
        # 调mem1.service.add()
        pass
    
    return response
```

#### `python/pool_router/providers.py`

增加内部调用helper：

```python
async def internal_chat_completion(
    messages: list[dict],
    model: str = "glm-4-flash",
    **kwargs
) -> str:
    """供mem1内部调用"""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{INTERNAL_BASE_URL}/v1/chat/completions",
            json={"messages": messages, "model": model, **kwargs}
        )
    return response.json()["choices"][0]["message"]["content"]
```

#### `python/pyproject.toml`

见上文3.1节。

---

## 五、新增依赖

### 5.1 核心依赖

| 依赖 | 版本 | 用途 |
|------|------|------|
| `numpy` | >=1.24 | EPA/残差/SVD计算 |
| `usearch` | >=2.0 | 向量索引 |
| `google-genai` | >=0.3 | Gemini embedding |
| `aiosqlite` | >=0.19 | 异步SQLite |

### 5.2 可选依赖

| 依赖 | 版本 | 用途 |
|------|------|------|
| `orjson` | >=3.9 | JSON性能优化 |
| `qdrant-client` | >=1.6 | 可选的向量数据库 |

### 5.3 安装命令

```bash
pip install numpy usearch google-genai aiosqlite orjson
```

---

## 六、数据库设计

### 6.1 "真相来源"裁决（必须写死）

**系统真相来源是：`tags` + `chunk_tag_map`**

- `tags_json`（如果保留）只能作为缓存/调试字段，不作为查询与boost的权威来源
- 任何写入流程必须保证：写入chunk_tag_map后（可选）再回填tags_json，且回填失败不影响检索

### 6.2 最小Schema（三表）

**chunks表**

| 字段 | 类型 | 说明 |
|------|------|------|
| chunk_id | TEXT PK | 主键 |
| scope_type | TEXT | user/agent/run |
| scope_id | TEXT | 作用域ID |
| source_path | TEXT | 原始文件路径或URI |
| source_mtime | REAL | 文件修改时间 |
| chunk_index | INTEGER | 文件内序号 |
| text | TEXT | 文本内容 |
| chunk_hash | TEXT | 幂等关键（hash(text + source_path + chunk_index)） |
| embedding | BLOB | 向量 |
| embedding_model | TEXT | 向量模型名 |
| created_at | REAL | 创建时间 |

**tags表**

| 字段 | 类型 | 说明 |
|------|------|------|
| tag_id | TEXT PK | 主键 |
| scope_type | TEXT | user/agent/run |
| scope_id | TEXT | 作用域ID |
| tag_text | TEXT | 规范化后的tag |
| tag_hash | TEXT | normalize(tag_text)的hash（可选） |
| embedding | BLOB | 向量 |
| embedding_model | TEXT | 向量模型名 |
| created_at | REAL | 创建时间 |

**chunk_tag_map表**

| 字段 | 类型 | 说明 |
|------|------|------|
| chunk_id | TEXT | 外键 |
| tag_id | TEXT | 外键 |
| weight | REAL | 权重（默认1.0） |
| origin | TEXT | 来源：tagger/rule/core/backfill |
| created_at | REAL | 创建时间（可选） |

**复合唯一键**：(chunk_id, tag_id)

### 6.3 索引策略与阶段性

| 阶段 | chunk侧 | tag侧 | 说明 |
|------|---------|-------|------|
| Phase A | 允许简化检索（线性/SQL），但必须保存embedding | 可选 | 先跑通流程 |
| Phase B | 同上 | 必须有tag_index（哪怕线性） | boost依赖tag召回 |
| Phase B后半 | 引入ANN（usearch）提升性能 | ANN优化 | 性能工程 |

**索引重建规则**：
- 何时full rebuild：embedding_model变更、索引损坏
- 何时增量upsert：新增/修改chunk或tag

### 6.4 完整SQL

```sql
-- 记忆块表
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    source_path TEXT,
    source_mtime REAL,
    chunk_index INTEGER,
    text TEXT NOT NULL,
    chunk_hash TEXT,
    embedding BLOB,
    embedding_model TEXT,
    created_at REAL DEFAULT (julianday('now'))
);

-- 标签表
CREATE TABLE IF NOT EXISTS tags (
    tag_id TEXT PRIMARY KEY,
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    tag_text TEXT NOT NULL,
    tag_hash TEXT,
    embedding BLOB,
    embedding_model TEXT,
    created_at REAL DEFAULT (julianday('now'))
);

-- 块-标签映射表
CREATE TABLE IF NOT EXISTS chunk_tag_map (
    chunk_id TEXT NOT NULL,
    tag_id TEXT NOT NULL,
    weight REAL DEFAULT 1.0,
    origin TEXT DEFAULT 'tagger',
    created_at REAL DEFAULT (julianday('now')),
    PRIMARY KEY (chunk_id, tag_id),
    FOREIGN KEY (chunk_id) REFERENCES chunks(chunk_id),
    FOREIGN KEY (tag_id) REFERENCES tags(tag_id)
);

-- 记忆历史表（用于history接口）
CREATE TABLE IF NOT EXISTS memory_history (
    id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL,
    event TEXT NOT NULL,
    old_memory TEXT,
    new_memory TEXT,
    created_at REAL DEFAULT (julianday('now'))
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_chunks_scope ON chunks(scope_type, scope_id);
CREATE INDEX IF NOT EXISTS idx_chunks_hash ON chunks(chunk_hash);
CREATE INDEX IF NOT EXISTS idx_tags_scope ON tags(scope_type, scope_id);
CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag_text);
CREATE INDEX IF NOT EXISTS idx_tags_hash ON tags(tag_hash);
CREATE INDEX IF NOT EXISTS idx_history_memory ON memory_history(memory_id);
```

---

## 七、Docker配置

```dockerfile
# python/mem1/Dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install -e .

COPY mem1/ ./mem1/

ENV MEM1_HOST=0.0.0.0
ENV MEM1_PORT=8001

EXPOSE 8001

CMD ["mem1"]
```

---

## 相关文档

- [VCP记忆系统概述](./claw_vcp_overview.md)
- [mem1 API规范](./claw_mem1_api_spec.md)
- [TagMemo算法迁移](./claw_tagmemo_algorithm.md)
- [mem1与FreePool Router集成](./claw_mem1_router_integration.md)
- [分阶段交付计划](./claw_mem1_phases.md)
