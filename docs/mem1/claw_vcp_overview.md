# VCP记忆系统概述

## 项目背景

在完成对 https://github.com/zeroclaw-labs/zeroclaw 的定制和增强过程中，主要有四件事要做：

1. **ZeroClaw加上OneBot通道** - 已初步完成，从反向ws连到NapCat上没问题
2. **实现能够独立运行的FreePool Router** - 已初步完成，解决了GLM模型实际表现与系统稳健性方面的细节
3. **把VCP的记忆系统搬过来** - 当前阶段
4. **把麦麦的消息时序能力搬过来** - 后续阶段

---

## 当前VCP环节的真实状态

### 已具备的关键前置条件

1. **FreePool Router已经是可跑的MVP**
   - 有OpenAI-compatible `/v1/chat/completions`（含流式）
   - 路由元信息解析
   - 难度初判+复核
   - GLM免费池 + Gemini池
   - endpoint inflight/cooldown/fallback
   - dry-run/mock/stub等
   - 这意味着已经有了"可控的上游调用层"，后面无论做embedding还是做RAG都能接入

2. **VCP的核心实现代码（记忆引擎）已以源码形式带进仓库**
   - `vcp-mem1/`下包含`KnowledgeBaseManager.js`、`EmbeddingUtils.js`、`EPAModule.js`、`Plugin/AgentDream/...`等完整链路
   - 代码材料是齐的，不是只剩文档
   - **注意**：vcp-mem1只是"参考材料"，不能直接改/跑/被调用

3. **对"无embedding版"已有明确实验路线**
   - 把`KnowledgeBaseManager`分成Vector Mode / Symbolic Mode
   - Symbolic Mode的落地pipeline：Query Parser → 倒排/BM25 → TagMemo-lite → LLM rerank → 拼装

### 还没完成的核心点

**VCP代码在仓库里，但它还没有变成ZeroClaw能稳定调用的memory backend / memory service。**

现在是"素材已入库"，但"接口层 + 运行闭环 + 运维姿势"还没落地。

---

## VCP记忆系统为什么强依赖embedding

VCP的"记忆强项"来自TagMemo的整条向量pipeline：

- 它不是"直接向量搜索"，而是**先对query向量做EPA/残差金字塔分析，再用标签向量重塑query，然后ANN搜索，最后做SVD/残差的新信息去重**
- 其中EPA（PCA/SVD）、Residual Pyramid（Gram-Schmidt投影/残差能量）、ResultDeduplicator（SVD+残差选新信息）这些步骤都要求：**query、tag、chunk在同一个连续向量空间里可线性代数操作**

这直接决定迁移策略：

- **想保留VCP的"味道"（EPA/残差/动态boost/去重）→ 就必须有embedding**
- **想彻底不embedding → 那就不是"搬VCP"，而是"借VCP的思想做Symbolic-TagMemo"**

---

## 已完成与未完成的工作

### 已完成/进展明确的部分

- ✅ VCP代码材料已进入仓库（`vcp-mem1`目录已包含核心文件）
- ✅ 已完成"上游调用层"的关键基础设施：FreePool Router MVP已能跑
- ✅ 已把"无embedding版本"的替代路线写成可执行pipeline

### 还没做/缺口最大的部分（P0）

1. ❌ **"ZeroClaw ↔ VCP Memory"的接口层还没落地**
   - 需要决定VCP在ZeroClaw世界里到底长什么样：
     - A) **sidecar服务**（Node进程跑VCP，ZeroClaw/Rust通过HTTP/IPC调用`search/write/status`）
     - B) **Rust内置backend**（把关键算法/存储搬到Rust memory模块里）

2. ❌ **embedding的调用与配置闭环还没打通**
   - VCP的默认实现里，入库（chunks/tags）和查询（query/seed）都需要`/v1/embeddings`这一层

3. ❌ **"写入链路"与"后台摄取链路"还没接到ZeroClaw的运行时**
   - VCP的强项之一是持续吸收本地文档/日记并做增量更新
   - ZeroClaw目前的memory写入点、什么时候触发写入、写什么、怎么避免污染，这些都需要明确接入策略

---

## 最该做的三件事（按优先级）

### 1. 定形态：VCP是sidecar还是内置backend？

建议先sidecar（最快闭环、最低侵入、最好调试）。

目标形态：做成类似 https://github.com/mem0ai/mem0 这样的独立组件。

### 2. 把embedding这件事"正规化"

不要幻想完全不embedding还能复刻TagMemo；可以省，但别删（至少保留：入库embedding + 查询embedding）。

embedding将使用 `gemini-embedding-001`。

### 3. 把"写入策略"补上

什么时候写记忆、写什么、怎么避免垃圾记忆——这比想象的更决定最终体验（否则引擎再强也会被喂坏）。

---

## mem0的启发

mem0提供了关键借鉴点：

1. **OpenAI-compatible代理能力**：它就是为了"上游不改/少改"而存在的
2. **Embedders可插拔（含Google AI/Vertex）**：这和指定`gemini-embedding-001`完全一致

所以做VCP组件（mem1）时，直接定目标：

- **先实现mem0的"形态"（API/代理/配置）**
- 再逐步把VCP的"内核"（TagMemo的重塑与去重）塞进去

---

## 相关文档

- [mem1 API规范](./claw_mem1_api_spec.md)
- [TagMemo算法迁移](./claw_tagmemo_algorithm.md)
- [mem1与FreePool Router集成](./claw_mem1_router_integration.md)
- [mem1实现指南](./claw_mem1_implementation.md)
- [分阶段交付计划](./claw_mem1_phases.md)
