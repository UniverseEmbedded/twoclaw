# 分阶段交付计划

本文档描述mem1记忆系统的分阶段交付计划，遵循"先能用，再做强"的原则。

---

## 总体原则

1. **每个阶段独立可用**：每个阶段完成后都能独立运行和验证
2. **增量迭代**：不一次性做所有功能，逐步叠加
3. **风险前置**：先解决最大的不确定性
4. **可回退**：每个阶段都是可回退的检查点

---

## Phase A：基础框架 + 简化检索

**目标**：mem0 REST API跑通，但检索先简化（不做TagMemo boost/去重）

### A.1 交付内容

| 内容 | 说明 |
|------|------|
| FastAPI服务骨架 | `server.py`、路由挂载 |
| mem0兼容API | `/configure`、`/memories`、`/search`、`/reset` |
| SQLite存储 | 三张表：chunks、tags、chunk_tag_map |
| Gemini Embedding | `gemini-embedding-001` |
| 简化检索 | 直接向量检索，不做boost/去重 |
| 基础健康检查 | `/healthz`、`/mem1/status` |

### A.2 验收标准

- [ ] `POST /memories` 能写入记忆并返回id
- [ ] `POST /search` 能检索到相关记忆
- [ ] `GET /mem1/status` 返回正确的统计信息
- [ ] 能用curl或Postman完成完整流程测试

### A.3 Phase A完成判据（必须落地成可验收的定义）

Phase A"完成"不仅是API跑通，必须满足以下条件：

1. **DB口径**：必须存在并被真实写入的三表：`chunks`、`tags`、`chunk_tag_map`
2. **幂等写入**：同一文件同一chunk多次ingest不应重复写入（用`chunk_hash`或同等机制）
3. **向量空间一致性**：chunks与tags的`embedding_model`必须记录并可在status中观察到
4. **回退机制**：tagger/tag_index不可用时，`/search`必须fallback到纯chunk检索（保证系统可用）

### A.4 依赖

- 无前置依赖

### A.5 预估工作量

1-2天

---

## Phase B：TagMemo Boost

**目标**：把VCP TagMemo Boost加回来，检索质量提升

### B.1 交付内容

| 内容 | 源文件 | 目标文件 |
|------|--------|----------|
| EPA分析 | `EPAModule.js` | `algorithms/epa.py` |
| 残差金字塔 | `ResidualPyramid.js` | `algorithms/residual.py` |
| TagMemo Boost | `KnowledgeBaseManager.js::_applyTagBoostV3` | `algorithms/tagmemo_boost.py` |
| 标签索引 | - | `index/usearch.py`（tag_index） |

### B.2 检索流程变化

**Phase A**：
```
query → embedding → 向量检索 → topK
```

**Phase B**：
```
query → embedding → EPA分析 → 残差金字塔 → TagMemo Boost → 向量检索 → topK
```

### B.3 Phase B硬依赖（必须写死）

Phase B（EPA/Residual/TagMemo Boost）依赖以下条件，缺一不可：

1. **tagger能稳定产出tags并落库**：tags表和chunk_tag_map表有真实数据
2. **tag_index已建立且可用于query→tags召回**：没有tag_index，EPA/Residual/Boost只是空转
3. **EPA/Residual/Boost必须基于相同embedding空间运行**：query/tag/chunk在同一向量空间
4. **启用Phase B的同时必须保持Phase A可回退**：当增强模块不可用时自动fallback

### B.4 验收标准（可观测 + 可控）

- [ ] EPA能输出logic_depth、entropy等特征
- [ ] 残差金字塔能输出coverage、novelty等特征
- [ ] TagMemo Boost能正确融合query和tag向量
- [ ] 检索结果比Phase A更相关（可通过A/B测试验证）

**可观测性要求**：
- `/search`需要产生最小debug信息：top tags、boostFactor、activation、entropy/logicDepth
- rag_params的调参流程可执行（默认值、如何收敛、如何回归）

### B.5 依赖

- Phase A完成（含三表真实写入、幂等写入、向量空间一致性）
- rag_params配置到位
- tag_index已建立

### B.6 预估工作量

2-3天

---

## Phase C：结果去重

**目标**：把VCP去重加回来，返回"新信息量最大"的topK

### C.1 交付内容

| 内容 | 源文件 | 目标文件 |
|------|--------|----------|
| 结果去重 | `ResultDeduplicator.js` | `algorithms/dedup.py` |

### C.2 检索流程变化

**Phase B**：
```
... → 向量检索 → topK
```

**Phase C**：
```
... → 向量检索 → topK*3 → SVD/残差去重 → topK
```

### C.3 验收标准

- [ ] 去重后结果不包含高度相似的重复内容
- [ ] 去重阈值可通过rag_params配置
- [ ] 检索结果多样性提升

### C.4 依赖

- Phase B完成

### C.5 预估工作量

1-2天

---

## Phase D：OpenAI代理层

**目标**：`/v1/chat/completions`的mem0式注入/回写

### D.1 交付内容

| 内容 | 说明 |
|------|------|
| OpenAI代理层 | `api/openai_proxy.py` |
| 记忆注入 | 在messages中注入检索到的记忆 |
| 自动回写 | 对话结束后自动写入记忆 |
| 开关控制 | 请求体/Header控制记忆行为 |

### D.2 使用方式

**方式一：请求体配置**
```json
{
  "model": "glm-4-flash",
  "messages": [...],
  "user_id": "u1",
  "memory": {
    "enabled": true,
    "writeback": true
  }
}
```

**方式二：Header**
```
X-Memory-Mode: readwrite
```

### D.3 验收标准

- [ ] 携带user_id的请求能自动检索记忆
- [ ] 记忆正确注入到messages中
- [ ] 对话结束后能自动回写
- [ ] 不携带user_id的请求不受影响

### D.4 依赖

- Phase C完成
- FreePool Router集成

### D.5 预估工作量

2-3天

---

## Phase E：增强功能（可选）

**目标**：工程化增强，提升可观测性和运维能力

### E.1 交付内容

| 内容 | 说明 |
|------|------|
| rag_params热更新 | `PUT /mem1/rag_params` |
| 索引重建 | `POST /mem1/reindex` |
| 批量导入 | `POST /mem1/ingest` |
| 记忆历史 | `GET /memories/{id}/history` |
| 监控指标 | Prometheus metrics |

### E.2 验收标准

- [ ] 能不重启服务更新rag_params
- [ ] 能重建损坏的索引
- [ ] 能批量导入日记/文档
- [ ] 能查看记忆的变更历史

### E.3 依赖

- Phase D完成

### E.4 预估工作量

2-3天

---

## 阶段依赖关系

```
Phase A ─→ Phase B ─→ Phase C ─→ Phase D ─→ Phase E
(基础)    (Boost)    (去重)     (代理层)    (增强)
```

每个阶段都是独立的里程碑，可以：
- 在任意阶段停止
- 在任意阶段发布
- 根据反馈调整后续阶段优先级

---

## 风险与缓解

### 风险1：Gemini Embedding API限制

**缓解**：
- 实现批量embedding
- 实现本地缓存
- 准备fallback方案（如OpenAI embedding）

### 风险2：TagMemo算法复杂度

**缓解**：
- Phase A先用简化版验证整体流程
- 算法迁移时保留调试输出
- 逐步迁移，每个组件独立测试

### 风险3：性能问题

**缓解**：
- 先用SQLite + USearch跑通
- 性能瓶颈出现后再优化
- 可选：热点迁到Rust

### 风险4：与ZeroClaw集成问题

**缓解**：
- Phase A-D先独立验证
- 集成前准备好测试用例
- 保持API兼容性

---

## 时间线（参考）

| 阶段 | 预估时间 | 累计 |
|------|----------|------|
| Phase A | 1-2天 | 1-2天 |
| Phase B | 2-3天 | 3-5天 |
| Phase C | 1-2天 | 4-7天 |
| Phase D | 2-3天 | 6-10天 |
| Phase E | 2-3天 | 8-13天 |

**建议**：从A→B→C→D逐步迭代，Phase E根据实际需求决定是否做。

---

## 检查点

### Phase A完成检查点

- [ ] 服务能启动
- [ ] 能写入记忆
- [ ] 能检索记忆
- [ ] 健康检查正常
- [ ] 三表（chunks/tags/chunk_tag_map）存在并被真实写入
- [ ] 幂等写入机制生效（chunk_hash）
- [ ] embedding_model可查询

### Phase B完成检查点

- [ ] EPA输出正确
- [ ] 残差金字塔输出正确
- [ ] TagMemo Boost生效
- [ ] 检索质量提升
- [ ] tag_index可用
- [ ] debug_info可观测
- [ ] Phase A回退机制正常

### Phase C完成检查点

- [ ] 去重生效
- [ ] 结果多样性提升
- [ ] 无明显性能退化

### Phase D完成检查点

- [ ] 代理层工作正常
- [ ] 记忆注入正确
- [ ] 自动回写正确
- [ ] 不影响非记忆请求

---

## 关键裁决

### 唯一真相来源与一致性

本项目中，标签相关的唯一真相来源为`tags`与`chunk_tag_map`。如保留`tags_json`，其仅用于缓存或调试展示，不可作为检索与TagMemo Boost的权威输入。写入流程必须确保tagger产出的标签写入`tags/chunk_tag_map`后再进行任何可选的回填操作，回填失败不影响系统可用性。

### Phase B的硬依赖与回退

Phase B（EPA/Residual/TagMemo Boost）依赖`tag_index`与稳定的tag资产（tags/chunk_tag_map）。当tagger、tag_index或任一增强模块不可用时，系统必须自动回退到Phase A的纯chunk相似度检索，保证`/search`始终可用。

---

## 相关文档

- [VCP记忆系统概述](./claw_vcp_overview.md)
- [mem1 API规范](./claw_mem1_api_spec.md)
- [TagMemo算法迁移](./claw_tagmemo_algorithm.md)
- [mem1与FreePool Router集成](./claw_mem1_router_integration.md)
- [mem1实现指南](./claw_mem1_implementation.md)
