# FreePool Router - API 设计与参考

## 概述

本文档描述 FreePool Router 的 API 设计方案，重点解决"如何在 API 请求里传递路由元数据"而不破坏 OpenAI 兼容性，并参考现有项目的实现方式。

---

## 核心问题

既然要做"难度定义 + 能力画像 + 多账号多模型调度"，那就一定会碰到一个架构选择：

1. **在 API 请求里显式带路由元数据**（Router 独立、ZeroClaw 改动少）
2. **把路由逻辑内置进 ZeroClaw**（上下文最完整、可做更深度策略）

建议采用 **"双层方案"**：先 API 元数据方案（外部 FreePool Router），再逐步把 ZeroClaw 内部的一些信号接进去。

---

## 推荐架构

### A. 外部 FreePool Router + 扩展请求元数据（优先）

- ZeroClaw 改动最小
- Router 可独立压测/迭代
- 以后 OpenClaw / 其它 Agent 也能复用
- 很适合同时搞 OneBot、时序、记忆、模型池这几条线

### B. ZeroClaw 内置路由（中后期）

当已经跑通 FreePool Router，且发现需要更多 ZeroClaw 内部信号（工具调用计划、记忆命中情况、agent阶段）时，再内置。

---

## API 怎么加"难度定义"才不破坏 OpenAI 兼容性？

有三种做法：

### 方案 1：用自定义 HTTP Header（最推荐）

```
x-freepool-route-hint
x-freepool-task-profile
x-freepool-priority
```

这类 header 方式在现有 AI Gateway 生态里是有先例的。比如 Portkey 风格网关会通过 `x-portkey-config` 这样的头来传路由配置，而客户端仍然调用 OpenAI-compatible 接口。

#### 优点

- 不改 OpenAI 请求体 schema
- 大多数 SDK 都支持加 header
- ZeroClaw 只要在 provider 层加 headers 就行（改动小）
- Router 可以完全独立

#### 缺点

- 结构化大对象放 header 不太优雅（可以 JSON 压缩/短字段化）
- 某些代理会限制 header 长度（一般 hint 不会太大）

---

### 方案 2：请求体加"扩展字段"（也可行）

在 `chat/completions` body 里加：

```
route_hints
task_profile
difficulty
```

很多 OpenAI-compatible 网关/代理对"未知字段"会选择忽略或透传，但这不是百分百保证。

#### 优点

- 结构化、清晰
- 调试方便（日志里一眼能看见）

#### 缺点

- 兼容性比 header 差一些
- 上游如果严格校验 schema，可能报错

---

### 方案 3：把 hint 编进 system message（不推荐）

例如在系统提示词里嵌"任务难度=xx、优先视觉推理模型"——这会污染 prompt，还会影响模型行为，不是路由层该做的事情。

---

## 推荐的具体做法

### 第一阶段：Header + 轻量 body 字段双轨

- **正式信号放 Header**（兼容优先）
- **调试镜像放 Body 扩展字段**（仅 Router 自己吃，必要时可关闭）

例如：

```
x-freepool-route-hint: lane=vision;difficulty=72;priority=high
x-freepool-task-profile: <base64(json)>
```

这样：

- 生产环境可以只保留 header
- 本地调试/回放时可以看 body 字段

---

## "难度定义"到底谁来产出？

难度不是 Router 单独就能完美判断的。因为 ZeroClaw 比 Router 知道更多上下文：

- 这是哪种 agent 阶段（规划、执行、总结）
- 是否会调用工具
- 当前工具链是否失败过
- 是否是记忆检索结果后的二次推理
- 是否是用户在群聊里的轻量闲聊 vs 高价值任务

### 两级难度定义

#### Level 1（Router 自己算）

Router 从 OpenAI 请求推断：

- 模态
- prompt tokens 估算
- max_tokens
- 是否图像输入
- 是否 JSON mode / tool call
- 关键词（代码/数学/总结）

得到一个 `router_estimated_difficulty`

#### Level 2（ZeroClaw 提示）

ZeroClaw 在调用时附带：

- `task_type`
- `difficulty_hint`
- `latency_tolerance`
- `quality_priority`
- `requires_reasoning`
- `requires_tool_reliability`

Router 最终融合：

```
final_difficulty = combine(router_estimate, zeroclaw_hint)
```

> 这样既保留 Router 独立性，也利用了 ZeroClaw 的"Agent 内部上下文"。

---

## Header 方案详细定义

### ZeroClaw 应传的关键信号

Router 能从请求内容推很多信息（长度、多模态数量等），但 ZeroClaw 知道的是"这个请求在 Agent 流程中的上下文位置"。这类信息最适合走 header。

#### 最值得传的 5 个字段

| Header | 说明 | 取值 |
|--------|------|------|
| `x-freepool-iteration` | 当前是第几轮工具循环 | 0, 1, 2... |
| `x-freepool-has-tools` | 这轮是否启用了工具能力 | 0, 1 |
| `x-freepool-agent-phase` | 请求所处阶段 | initial, tool_followup, repair, finalize |
| `x-freepool-tool-error-count` | 本轮前累计工具失败次数 | 0, 1, 2... |
| `x-freepool-budget-policy` | 预算策略（必须传，Router猜不出来） | free_only, prefer_free_then_credit, credit_allowed, premium_only |

#### 可选补充字段

| Header | 说明 | 取值 |
|--------|------|------|
| `x-freepool-quality-intent` | 用户期望质量/速度取向 | fast, balanced, high |
| `x-freepool-context-kind` | 请求依赖的外部上下文来源 | none, memory, tool, mixed |
| `x-freepool-memory-hit` | 是否命中记忆 | 0, 1 |
| `x-freepool-tool-result-injected` | 是否注入了工具结果 | 0, 1 |

#### 不值得传的字段（Router自己能推）

- 文本长度（Router自己算）
- 图片数量（Router可解析内容）
- 是否多模态（Router可解析）
- 请求模型名（本来就在请求里）
- 是否 stream（请求参数里有）

### 完整 Header 字段规范

```
x-freepool-iteration: 0|1|2...
x-freepool-has-tools: 0|1
x-freepool-agent-phase: initial|tool_followup|repair|finalize
x-freepool-tool-error-count: 0|1|2...
x-freepool-budget-policy: free_only|prefer_free_then_credit|credit_allowed|premium_only
x-freepool-quality-intent: fast|balanced|high
x-freepool-context-kind: none|memory|tool|mixed
x-freepool-lane: text_fast|reasoning|vision|tool_heavy|long_context|image_gen|video_gen|tts|embeddings
x-freepool-difficulty: 0-100
x-freepool-priority: low|normal|high
x-freepool-quality-priority: low|balanced|high
x-freepool-latency-tolerance: low|medium|high
x-freepool-requires-thinking: 0|1
x-freepool-task-profile: base64(json)（可选，调试期）
x-freepool-max-estimated-cost-usd: 0.02
x-freepool-value-tier: low|normal|high
x-freepool-trace-id: <uuid>（请求追踪）
x-freepool-scene: chat|agent_step|tool_summary|vision_parse|image_task|video_task
x-freepool-user-tier: guest|member|admin
```

### 最小头部集合（推荐）

如果想保持简洁，先只传这 5 个：

```
x-freepool-iteration
x-freepool-has-tools
x-freepool-agent-phase
x-freepool-tool-error-count
x-freepool-budget-policy
```

这 5 个都是 Router 很难从请求体可靠推断出来、但对路由决策非常有价值的信息。

### 更务实的版本（最少）

最小到不能再小，可以只有这 3 个：

```
x-freepool-iteration
x-freepool-agent-phase
x-freepool-budget-policy
```

### Body 扩展字段（仅调试 / 自用）

```json
{
  "model": "auto",
  "messages": [...],
  "max_tokens": 2048,
  "_freepool": {
    "lane": "reasoning",
    "difficulty": 68,
    "priority": "high"
  }
}
```

> 生产时可关掉 body 扩展，只保留 header，兼容性更稳。

---

## 现有项目参考

### 1) LiteLLM（最值得参考的开源路由器之一）

LiteLLM 有成熟的：

- 路由（Router）
- 负载均衡
- fallback
- retries
- 多策略（weighted、least-busy、rate-limit aware、latency-based、cost-based 等）
- OpenAI-compatible 代理能力

#### 可以借鉴

- **"模型组（model_group）"概念**：同类能力的多个部署统一管理
- **Rate-limit aware 路由**
- **fallback 链配置**
- **把路由和 provider 抽象分开**

#### 不完全满足的地方

- 更偏"通用多 provider 网关"
- 不一定天然理解"智谱免费模型池 + 多账号 + 稀缺并发模型保护"
- "任务难度评分"通常需要自己扩展策略

> 结论：**非常值得参考架构和策略，但仍需要自定义 FreePool 策略器。**

---

### 2) Portkey Gateway（头部配置 / 路由控制的思路）

Portkey 的网关强调：

- 统一 API
- retries / fallbacks / load balancing
- 多模态支持
- 通过配置控制路由

搜索结果里还直接展示了通过 `x-portkey-config` header 传配置的用法（这是"API层带路由定义"的现实参考）。

#### 可以借鉴

- **Header 携带路由策略** 的模式
- 网关作为独立层的工程做法
- 多模态统一入口设计

#### 不足

- 目标更细（免费额度最大化、账号池配额保护）
- 还需要 Agent-aware 的难度/能力路由逻辑

---

### 3) Bifrost（高性能 AI Gateway）

Bifrost 这类项目主打：

- OpenAI-compatible
- 自适应负载均衡
- 高性能低开销
- 多 provider 统一入口

#### 可以借鉴

- **网关层工程化能力**（性能、集群、观测）
- 自适应负载均衡的思路

#### 不足

- 当前更需要"策略正确性"而不是 5k RPS 性能
- 路由是"任务难度 + 模型能力画像 + 免费额度/并发池"，偏策略层

---

### 4) semantic-router（语义路由层）

`semantic-router` 不是 API Gateway，而是"超快语义决策层"，用于在生成前做路由/分流。它的定位就是在 LLM/Agent 前做智能决策。

#### 可以借鉴

- **把"任务分类/路由判定"单独做成前置层**
- 用 embedding / 语义空间来判断任务类型（比纯关键词好）

#### 怎么用于本项目

- 用在 `lane` 分类（text fast / reasoning / vision / long-context）
- 难度辅助分类（轻/中/重）
- 但不一定要直接用这个库（尤其想用 Rust + ZeroClaw）

---

### 5) RouteLLM / LLMRouter（偏"强弱模型路由研究"）

这类项目更偏研究/实验，核心思想是：

- 识别"简单问题 vs 复杂问题"
- 将简单问题路由到便宜模型、复杂问题到强模型

这和"能力分 + 难度分"非常同构。

#### 可以借鉴

- 难度路由框架
- 成本-质量权衡（cost-quality tradeoff）
- 评测思路（不是只看成功率，还看成本/延迟/质量）

#### 不足

- 场景是**多账号+免费模型池+并发限制+多模态**
- 需要更强工程实现，而不只是二分类路由

---

## 实现路线建议

### Phase 1（现在）

**外部 FreePool Router + Header 路由 hints**

- ZeroClaw 改很少
- 可以先把 OneBot Channel、记忆、时序并行推进
- 参考 LiteLLM / Portkey 的路由与 header 思路来做自己的版本

### Phase 2（跑稳定后）

给 ZeroClaw 增加一个轻量"路由提示生成器"（不是内置全路由器）：

- 在 provider 调用前生成 `difficulty_hint/task_profile`
- 放到 header 里给 FreePool Router

### Phase 3（如果真需要）

再考虑把 Router 内置进 ZeroClaw 或做成 ZeroClaw provider：

- 当需要访问更多内部信号且外部 router 不够用时

---

## 参考链接

- [LiteLLM Routing & Load Balancing](https://docs.litellm.ai/docs/routing-load-balancing) - 路由与负载均衡文档
- [Portkey Gateway](https://github.com/Portkey-AI/gateway) - Header 配置参考
- [Bifrost AI Gateway](https://github.com/maximhq/bifrost) - 高性能网关
- [semantic-router](https://github.com/aurelio-labs/semantic-router) - 语义路由层
- [RouteLLM](https://pypi.org/project/routellm/) - 强弱模型路由研究
