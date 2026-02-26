# TagMemo算法迁移

本文档描述如何将VCP TagMemo V3.7的核心算法从`vcp-mem1`迁移到Python实现的mem1系统。

---

## 一、核心算法组件概览

TagMemo的核心是一个多阶段的检索增强流程：

```
Query → EPA分析 → 残差金字塔 → TagMemo Boost → ANN检索 → 结果去重 → TopK
```

### 组件清单

| 组件 | 源文件 | 目标文件 | 功能 |
|------|--------|----------|------|
| EPA | `EPAModule.js` | `epa.py` | Embedding投影分析 |
| 残差金字塔 | `ResidualPyramid.js` | `residual_pyramid.py` | 残差能量计算 |
| TagMemo Boost | `KnowledgeBaseManager.js::_applyTagBoostV3` | `tagmemo_boost.py` | 向量重塑 |
| 结果去重 | `ResultDeduplicator.js` | `dedup.py` | SVD/残差选新信息 |
| 文本切块 | `TextChunker.js` | `chunker.py` | 文本分块 |
| Embedding工具 | `EmbeddingUtils.js` | `embedder_gemini.py` | 向量化 |

---

## 二、写入链路

### 2.1 切块（Chunking）

**源文件**：`vcp-mem1/TextChunker.js`

**目标文件**：`pool_router/mem1/chunker.py`

**输出**：
```python
@dataclass
class Chunk:
    text: str
    start: int
    end: int
    hash: str
    ts: datetime
    scope_type: str  # user/agent/run
    scope_id: str
```

**迁移要点**：
- 保持VCP的切块策略（按段落/句子边界切分）
- 保留hash计算方式（用于增量更新检测）
- 支持配置chunk大小和重叠

---

### 2.2 标签生成（Tagging）

**源文件**：`vcp-mem1/diary-tag-batch-processor.js`（思路）

**目标文件**：`pool_router/mem1/tagger_llm.py`

**实现方式**：用LLM生成tags（走FreePool Router的`/v1/chat/completions`）

**Prompt产出**：
- tags: 标签列表
- importance: 重要性评分
- category: 分类
- language/world: 语言/世界观（可选）

**规则清洗**：
- 技术词门槛（techTagThreshold）
- 语言补偿（languageCompensator）

---

### 2.3 Embedding

**源文件**：`vcp-mem1/EmbeddingUtils.js`（接口思想）

**目标文件**：`pool_router/mem1/embedder_gemini.py`

**固定模型**：`gemini-embedding-001`

**接口**：
```python
class GeminiEmbedder:
    async def embed_texts(self, texts: list[str]) -> np.ndarray:
        """
        输入: 文本列表
        输出: float32向量数组, shape=(len(texts), embedding_dim)
        """
        pass
    
    async def embed_single(self, text: str) -> np.ndarray:
        """单个文本embedding"""
        pass
```

---

### 2.4 存储与索引

**源文件**：`vcp-mem1/KnowledgeBaseManager.js`（表结构概念）

**目标文件**：
- `store_sqlite.py`：SQLite表结构 + CRUD
- `index_usearch.py`：USearch向量索引封装

**最小表结构**：

```sql
-- 记忆块表
CREATE TABLE memo_chunks (
    id TEXT PRIMARY KEY,
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    text TEXT NOT NULL,
    ts REAL NOT NULL,
    vector BLOB,
    tags_json TEXT,
    meta_json TEXT,
    hash TEXT
);

-- 标签表
CREATE TABLE tags (
    id TEXT PRIMARY KEY,
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    tag TEXT NOT NULL,
    vector BLOB,
    count INTEGER DEFAULT 1,
    last_ts REAL
);

-- 块-标签映射表
CREATE TABLE chunk_tag_map (
    chunk_id TEXT NOT NULL,
    tag_id TEXT NOT NULL,
    weight REAL DEFAULT 1.0,
    PRIMARY KEY (chunk_id, tag_id)
);

-- 索引
CREATE INDEX idx_chunks_scope ON memo_chunks(scope_type, scope_id);
CREATE INDEX idx_tags_scope ON tags(scope_type, scope_id);
CREATE INDEX idx_tags_tag ON tags(tag);
```

**向量索引**：
- `chunk_index`：向量检索chunk
- `tag_index`：向量检索tag（TagMemo要用）

---

## 三、检索链路（核心）

### 3.1 EPA（Embedding Projection Analysis）

**源文件**：`vcp-mem1/EPAModule.js`

**目标文件**：`mem1/epa.py`

**功能**：对query向量做PCA/SVD分析，提取语义特征

**输出**：
```python
@dataclass
class EPAFeatures:
    logic_depth: float      # 逻辑深度
    entropy: float          # 熵
    dominant_axes: list     # 主轴方向
    query_world: str        # 推断的世界观/领域
```

**算法要点**：
- 对query向量做SVD分解
- 计算奇异值分布得到logic_depth和entropy
- 根据主轴方向推断query所属的语义空间

---

### 3.2 残差金字塔（Residual Pyramid）

**源文件**：`vcp-mem1/ResidualPyramid.js`

**目标文件**：`mem1/residual_pyramid.py`

**功能**：通过Gram-Schmidt投影计算残差能量

**输出**：
```python
@dataclass
class ResidualFeatures:
    coverage: float            # 覆盖度
    novelty: float             # 新颖度
    depth: float               # 深度
    tag_memo_activation: float # TagMemo激活度
```

**算法要点**：
- 将query向量投影到已知语义空间
- 计算残差（投影后的剩余部分）
- 残差能量反映query的新颖程度
- tag_memo_activation决定是否启用TagMemo增强

---

### 3.3 TagMemo Boost（向量重塑）

**源文件**：`vcp-mem1/KnowledgeBaseManager.js::_applyTagBoostV3`

**目标文件**：`mem1/tagmemo_boost.py`

**功能**：用标签向量重塑query向量

**输入**：
```python
query_vector: np.ndarray      # 原始query向量
base_tag_boost: float         # 基础tag boost系数
core_tags: list[str] | None   # 核心标签（可选）
rag_params: dict              # RAG参数
```

**输出**：
```python
@dataclass
class BoostResult:
    fused_vector: np.ndarray  # 融合后的向量
    debug_info: dict          # 调试信息
```

**算法流程**：
1. **标签感应**：用query向量检索topN相关标签
2. **动态boost计算**：
   - 根据EPA的entropy调整boost强度
   - 根据残差金字塔的activation调整
   - 应用dynamicBoostRange约束
3. **核心标签聚光灯**：
   - 如果有coreTags，额外增强
   - 应用coreBoostRange
4. **向量融合**：
   - `fused = query + boost_factor * tag_vector_sum`
   - 归一化

---

### 3.4 结果去重（新信息最大化）

**源文件**：`vcp-mem1/ResultDeduplicator.js`

**目标文件**：`mem1/dedup.py`

**功能**：对候选chunks做SVD/残差分析，保留"增量信息"最大的K条

**算法要点**：
1. 将候选chunks的向量组成矩阵
2. 做SVD分解
3. 计算每条chunk相对于已选chunks的残差
4. 选择残差最大（新信息最多）的chunk
5. 重复直到选够K条

**接口**：
```python
def deduplicate_results(
    candidates: list[Chunk],
    query_vector: np.ndarray,
    k: int,
    threshold: float
) -> list[Chunk]:
    """
    从candidates中选择新信息量最大的k条
    threshold: 相似度阈值，超过则认为重复
    """
    pass
```

---

### 3.5 整体检索流程

**目标文件**：`mem1/service.py::search(...)`

```python
async def search(
    query: str,
    scope: Scope,
    filters: dict | None = None,
    limit: int = 10
) -> list[SearchResult]:
    # 1. Query embedding
    query_vector = await embedder.embed_single(query)
    
    # 2. EPA分析
    epa_features = epa.analyze(query_vector)
    
    # 3. 残差金字塔
    residual_features = residual_pyramid.analyze(query_vector, scope)
    
    # 4. TagMemo Boost（如果激活）
    if residual_features.tag_memo_activation > threshold:
        boost_result = tagmemo_boost.boost(
            query_vector, 
            epa_features, 
            residual_features,
            rag_params
        )
        search_vector = boost_result.fused_vector
    else:
        search_vector = query_vector
    
    # 5. ANN检索
    candidates = await index.search(search_vector, top_k=limit*3)
    
    # 6. 结果去重
    results = deduplicate_results(
        candidates, 
        query_vector, 
        limit,
        rag_params['deduplicationThreshold']
    )
    
    return results
```

---

## 四、rag_params参数体系

### 4.1 参数来源

- `vcp-mem1/rag_params.json`
- `vcp-mem1/TAGMEMO_TUNING_GUIDE.md`

### 4.2 默认值

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

### 4.3 参数含义

| 参数 | 含义 | 使用位置 |
|------|------|----------|
| `noise_penalty` | 熵惩罚项 | EPA/是否启动强TagMemo |
| `tagWeightRange` | tag向量能量比例β的范围 | TagMemo Boost |
| `tagTruncationBase` | 感应阶段保留标签基准比例 | 标签感应 |
| `tagTruncationRange` | 感应阶段保留标签比例范围 | 标签感应 |
| `activationMultiplier` | 残差金字塔activation对boost的增益 | TagMemo Boost |
| `dynamicBoostRange` | dynamicBoostFactor的clamp范围 | TagMemo Boost |
| `coreBoostRange` | 核心标签聚光灯动态范围 | TagMemo Boost |
| `deduplicationThreshold` | 去重相似度阈值 | ResultDeduplicator |
| `techTagThreshold` | 技术词清洗门槛 | 标签生成 |
| `normalTagThreshold` | 普通词清洗门槛 | 标签生成 |
| `languageCompensator.*` | 跨语言/未知世界惩罚 | 标签权重调整 |

---

## 五、迁移注意事项

### 5.1 向量空间一致性

EPA/残差/TagMemo Boost都要求query、tag、chunk在同一个连续向量空间。因此：
- 入库embedding和查询embedding必须用同一个模型
- 不能混用不同维度的embedding模型

### 5.2 数值精度

- JavaScript的Float64 vs Python的float32/float64
- 向量归一化要在同一精度下进行
- SVD计算注意数值稳定性

### 5.3 异步处理

VCP原实现是同步的，Python版需要：
- embedding调用是异步的
- 数据库操作用aiosqlite
- 索引操作可能需要线程池

### 5.4 调试支持

保留VCP的调试输出能力：
- 每个阶段输出debug_info
- 支持trace级别日志
- 可选的中间结果导出

---

## 六、调试与调参指南（最小版）

### 6.1 A/B对比方法

**目标**：验证boost是否让结果变好

**步骤**：
1. 准备固定测试集（20-50个query，来自真实对话/日记）
2. 运行两组检索：
   - A组：boost off（直接用query向量检索）
   - B组：boost on（用fused_vector检索）
3. 对比指标：
   - 人工打分：相关性评分（1-5分）
   - hit@k：前K条中包含正确答案的比例
   - 多样性：结果之间的语义差异度

### 6.2 必须观察的debug_info

每次`/search`应输出以下信息（可通过日志或响应中的可选字段）：

| 字段 | 来源 | 说明 |
|------|------|------|
| activation | ResidualPyramid | TagMemo激活度 |
| entropy | EPA | 熵（归一化后） |
| logicDepth | EPA | 逻辑深度（1 - entropy） |
| dynamicBoostFactor | TagMemo Boost | 动态boost系数（clamp前后） |
| top_tags | TagMemo Boost | topN标签（id/text/score） |
| fused_vector_cos_sim | TagMemo Boost | fused_vector与原query的余弦相似度 |

**用途**：
- activation过低 → 可能不需要boost
- dynamicBoostFactor过高 → boost过猛，需要调参
- fused_vector_cos_sim过低 → boost方向偏离query

### 6.3 调参顺序建议

**第一阶段：限制上限防炸**
1. 先调整`dynamicBoostRange`：限制boost上限（如[0.3, 1.5]）
2. 观察`dynamicBoostFactor`分布，确保不常触及上限

**第二阶段：调响应灵敏度**
3. 调整`activationMultiplier`：控制activation对boost的影响
4. 观察activation与dynamicBoostFactor的相关性

**第三阶段：调核心标签锚定强度**
5. 调整`coreBoostRange`：控制核心标签的额外增强
6. 观察coreTags是否在top_tags中出现

**第四阶段：调跨语言/技术词惩罚**
7. 调整`languageCompensator.*`：控制跨语言/技术词的权重
8. 观察技术词tag的权重变化

### 6.4 常见问题与解决

| 问题 | 可能原因 | 解决方案 |
|------|----------|----------|
| boost后结果变差 | dynamicBoostRange过大 | 降低上限 |
| 结果过于集中 | activationMultiplier过高 | 降低activation影响 |
| 核心标签丢失 | coreBoostRange过低 | 提高核心标签增强 |
| 技术词tag权重过高 | languageCompensator未生效 | 检查query_world推断 |
| 结果多样性差 | 去重阈值过低 | 提高deduplicationThreshold |

### 6.5 回归测试

每次调参后，运行固定测试集验证：
- 相关性不应下降
- 多样性不应明显下降
- 延迟不应明显增加

---

## 相关文档

- [VCP记忆系统概述](./claw_vcp_overview.md)
- [mem1 API规范](./claw_mem1_api_spec.md)
- [mem1与FreePool Router集成](./claw_mem1_router_integration.md)
- [mem1实现指南](./claw_mem1_implementation.md)
- [分阶段交付计划](./claw_mem1_phases.md)
