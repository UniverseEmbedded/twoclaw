# FreePool Router 核心架构设计

## 概述

FreePool Router（免费模型池调度器）是一个面向免费额度与并发限制的推理调度层，目标：

- 利用 **多个账号 × 多个免费模型** 的总吞吐
- 保证 **模态匹配 / 上下文匹配 / 输出上限匹配**
- 在限流、拥塞、错误时自动降级/切换
- 尽量把"简单任务"打给便宜高并发模型，把"困难任务"打给稀缺强模型

---

## 总体架构

做成一个 **OpenAI-compatible 本地路由服务**，让 ZeroClaw 只连它一个 endpoint（ZeroClaw 本身支持 OpenAI-compatible/provider 可替换路线，这样侵入最小）。

### 架构层次

- **ZeroClaw**
  - provider 指向本地 router（例如 `http://127.0.0.1:8787/v1/chat/completions`）
- **FreePool Router**
  - 账号池（多个智谱 API key）
  - 模型池（文本/视觉/生成）
  - 调度器（评分 + 限流 + fallback）
  - 队列（按模型/账号）
  - 指标与状态（成功率、延迟、限流频率）
- **BigModel / ZAI**
  - 实际上游 API

> 这样做的好处：ZeroClaw 不需要知道你有 3 个账号、8 个模型、每个并发不同。

---

## 两阶段调度流程

调度不是"随机轮转"，而是"两阶段选择"。

### 阶段 A：硬约束过滤（必须先做）

先筛掉不可能的候选模型：

#### 输入侧约束

- 模态匹配（文本 / 图文 / 文件 / 视频）
- 上下文长度上限（估算 prompt tokens）
- 输出上限（max_tokens）
- 是否需要工具调用 / JSON / thinking / 视觉推理
- 是否需要长上下文缓存（可选）

#### 账号/运行态约束

- 该账号该模型当前是否可用（健康）
- 并发槽是否有空位
- 是否在 cooldown（刚触发 1302/1305）
- 今日/周/月额度是否已接近阈值（如果能观测到）

通过这一步后，剩下的是"可打的模型候选集"。

---

### 阶段 B：软评分排序

对候选模型打分，选最高分（或 top-k 里做带权随机）。

#### 评分函数

```
FinalScore = FitScore + CapabilityScore + AvailabilityScore + CostScarcityScore + StabilityScore - RiskPenalty
```

具体拆解：

#### 1) FitScore（任务适配度）

看任务需求与模型属性是否匹配：

- 需要视觉：4.6V / 4.1V / 4V 才有分
- 需要高推理：Z1 / 4.7 / 4.6V（thinking on）加分
- 需要超长上下文：4.7 优先，4-Flash-250414 次之
- 简单短问答：高并发低成本模型（4-Flash / 4-Flash-250414）加分

#### 2) CapabilityScore（能力分）

按任务类型维度维护，而不是一个总分：

- `coding_agent`
- `reasoning_math`
- `chat_general`
- `long_context`
- `vision_ocr_doc`
- `vision_reasoning`
- `tool_use`
- `creative_writing`
- `image_gen`
- `video_gen`

每个模型一组分数，0~100。

#### 3) AvailabilityScore（可用性/拥塞分）

动态分数，强烈建议做：

- 当前队列长度越短越高
- 当前并发占用率越低越高
- 最近 1 分钟限流率越低越高
- 最近 p95 延迟越低越高

#### 4) CostScarcityScore（稀缺资源保护）

这是很多人会忽略的，但这个场景非常关键。

比如：

- `GLM-4.7-Flash` 并发=1（当前账户视角）
- `GLM-4.6V-Flash` 并发=1
- `GLM-Z1-Flash` 并发高
- `GLM-4-Flash` 高并发但长上下文时有效并发会塌陷

那么就应该给"稀缺强模型"加一个**保护机制**：

- 简单任务不轻易占用 4.7
- 把 4.7 留给长上下文/复杂 agent 编排/高价值请求
- 高并发推理请求优先打 Z1 或旧 Flash

#### 5) StabilityScore（稳定性）

按运行时统计：

- 成功率
- 结构化输出成功率
- 工具调用成功率
- 重试率

#### 6) RiskPenalty（风险惩罚）

例如：

- 上下文接近上限（>85%）
- 该模型最近连续 1305（拥塞）
- 该账号最近连续 1302（账号限流）
- 视觉任务但图片很大/很多页，超过模型舒适区

---

## 任务难度评分

### TaskProfile 定义

在 router 里给每次请求先做一个 profile：

- `modality`: text / vision / image_gen / video_gen
- `prompt_tokens_est`
- `requested_output_tokens`
- `has_tools`
- `requires_json`
- `requires_thinking`
- `conversation_turns`
- `file_count`
- `image_count`
- `doc_pages_est`（可估）
- `is_coding`
- `is_math_logic`
- `is_longform`
- `priority`（可选，系统任务/用户任务）

然后生成：

- `difficulty_score`（0~100）
- `latency_tolerance`（低/中/高）
- `quality_priority`（低/中/高）

### 简单启发式规则

- 长上下文（>32k） +20
- 工具调用 +15
- JSON 强约束 +10
- 代码任务 +15
- 数学/逻辑 +20
- 多图/文档 +20
- 请求输出 >8k +10
- 普通闲聊 -20
- 单轮短问答 -15

然后：

- `difficulty < 25` → 轻任务
- `25~60` → 中任务
- `>60` → 重任务

这样就能把"轻任务"尽量导到高并发模型，把"重任务"导到 4.7 / 4.6V / Z1。

---

## 账号池调度

要最大化的是 **账号 × 模型** 总容量，所以候选实体应该是：

**`Endpoint = (account_id, model_id)`**

而不是只看模型。

### 每个 Endpoint 要维护的状态

- `configured_concurrency`
- `inflight`
- `cooldown_until`
- `recent_1302_count`
- `recent_1305_count`
- `success_rate_5m`
- `p95_latency_5m`
- `avg_tps`（可选）
- `last_used_at`

### 账号级别状态

因为有些限制是"账号级"：

- `account_rate_limited_until`
- `daily_quota_remaining_est`（估计值）
- `weekly/monthly usage`（如能观测）

---

## 关键调度策略

### 策略 1：分层路由（比全局打分更稳）

先按任务类型走不同策略器，再在策略器内打分：

- `TextFastLane`（简单文本）
- `ReasoningLane`（推理/代码）
- `VisionLane`（图文/文档）
- `ImageGenLane`
- `VideoGenLane`

这样不会出现"拿文本模型去跟视觉模型竞争"的混乱评分。

### 策略 2：拥塞退避 + 熔断（必须做）

遇到官方常见错误码：

- `1302`（账户速率限制）
- `1305`（模型访问量过大）
- `1304/1308/1310`（额度类上限）

建议行为：

- `1302`：**账号-模型 endpoint cooldown**（短）
- `1305`：**模型级 cooldown**（对该模型所有账号短退避）
- `1304/1308/1310`：**账号级长熔断**，直到下一刷新窗口

### 策略 3：保留"稀缺强模型"的预算

例如给 `GLM-4.7-Flash` 设置：

- `reserve_for_high_difficulty = true`
- `min_difficulty = 55`
- `max_queue_len = 2`
- `prefer_when_context > 64k`

否则它会被普通聊天挤爆。

同理 `GLM-4.6V-Flash`（并发稀缺）要给：

- 仅视觉任务可用
- 对"单图简单问答"优先降到 `4V-Flash`
- 对"多页文档/复杂图表推理"才用 `4.6V-Flash`

### 策略 4：同请求多候选"试探式"选择（可选）

对高价值任务可做：

- 先选主模型
- 备选模型列表（fallback chain）
- 若超时/限流/结构化失败，快速切备选

这比单次失败后重新全局调度更稳。

---

## 模型能力画像配置

用一个 YAML/TOML 文件维护（便于手调）：

```toml
[models.glm-4.7-flash]
modalities = ["text"]
max_context = 200000
max_output = 128000
thinking = "toggle"
tool_call = true
base_concurrency = 1
scores = { coding_agent = 92, reasoning_math = 88, chat_general = 82, long_context = 95, tool_use = 90 }

[models.glm-z1-flash]
modalities = ["text"]
max_context = 32000
max_output = 32000
thinking = "forced"
tool_call = false
base_concurrency = 30
scores = { coding_agent = 78, reasoning_math = 93, chat_general = 60, long_context = 40, tool_use = 50 }

[models.glm-4.6v-flash]
modalities = ["text","image","file","video"]
max_context = 128000
max_output = 32000
thinking = "toggle"
tool_call = true
base_concurrency = 1
scores = { vision_ocr_doc = 90, vision_reasoning = 94, tool_use = 88, long_context = 86 }
```

---

## 评分函数实现

```text
score =
  0.35 * capability_fit +
  0.20 * hard-fit-margin +
  0.20 * availability +
  0.15 * stability +
  0.10 * scarcity_policy
```

其中 `scarcity_policy` 可以是负分（保护稀缺模型）或正分（高难任务允许用）。

---

## 实现到 ZeroClaw 的两种方式

### 方案 A（推荐）：外部 Router（最稳）

**优点**

- 不深改 ZeroClaw
- 迭代快
- 可单独压测
- 以后不止 ZeroClaw 能用（OpenClaw / 自研 agent 也能用）

**ZeroClaw 配置方式**

- 用 OpenAI-compatible provider/base_url 指向 router

### 方案 B（中期）：做成 ZeroClaw 内置 provider/router

在 `src/providers/` 新增一个 `glm_pool` provider：

- 内部调用调度逻辑
- 外部对 ZeroClaw 看起来像单 provider

**优点**

- 更统一
- 能更深结合 hooks / memory / agent metadata

**缺点**

- 开发和维护成本高
- 升级 ZeroClaw 时冲突更多

---

## 观测指标

至少记录这些（按 endpoint = 账号+模型）：

- 请求数 / 成功率 / 失败率
- 错误码分布（1302/1305/...）
- 平均延迟 / p95 / p99
- 平均输出 tokens
- 平均输入 tokens
- 并发占用率
- 排队时长
- 重试次数
- fallback 触发率
- 结构化输出成功率（如果有 JSON/tool call）

然后你会很快发现：

- 哪个模型纸面强但实际拥塞严重
- 哪个账号被限流更频繁
- 哪类任务经常被错分到不合适模型

---

## 实战默认路由规则

### 文本任务

- **轻任务（difficulty < 25）**
  优先：`GLM-4-Flash` / `GLM-4-Flash-250414`
  备选：`GLM-4.7-Flash`（仅队列空时）
- **中任务（25~60）**
  优先：`GLM-4-Flash-250414`
  推理/代码偏强：`GLM-Z1-Flash`
  长上下文：`GLM-4.7-Flash`
- **重任务（>60）**
  优先：`GLM-4.7-Flash`
  数学/逻辑密集：`GLM-Z1-Flash`（若上下文够）
  拥塞时降级：`GLM-4-Flash-250414`

### 视觉任务

- **简单单图问答**
  优先：`GLM-4V-Flash`
- **复杂图表/多步视觉推理**
  优先：`GLM-4.1V-Thinking-Flash`
- **长文档/多页图文/需工具调用**
  优先：`GLM-4.6V-Flash`
- **4.6V 拥塞时**
  降级：`4.1V-Thinking-Flash`（如果上下文/任务允许）

---

## 常见陷阱

### 坑 1：只看并发，不看"有效并发"

`GLM-4-Flash` 长上下文时并发塌陷，这就是典型"有效并发"问题。
解决：把 `configured_concurrency` 改成 **`effective_concurrency(task_profile)`**。

### 坑 2：把所有任务都送 4.7

会导致：

- 低吞吐
- 排队爆炸
- 用户体感变差
- 免费资源浪费

### 坑 3：没有 cooldown / 熔断

没有这个，遇到限流会疯狂重试，把所有账号一起打爆。

### 坑 4：不记录 task_profile 与路由结果

后面没法回放"为什么这次选错模型"。

---

## MVP 落地顺序

1. **静态模型表**（用模型参数表初始化）
2. **账号池配置**（多 key）
3. **硬约束过滤**
4. **按模型/账号并发计数 + 队列**
5. **错误码退避（1302/1305/1304/1308/1310）**
6. **简单评分（能力分 + 拥塞分 + 稀缺保护）**
7. **指标记录**
8. **再加任务难度分与 lane 路由**

> 不要一开始就做"机器学习路由器"。启发式 + 指标反馈已经会非常强。

---

## 难度分类二次确认机制

### 核心原则

**凡是"初判为低难"的请求，一律再过一遍 GLM-4-Flash 做难度复核。**

这是为了防止"短句高难"的误判：
- 用户只说一句短话
- 但实际任务很难（如"证明这个算法是对的"、"设计一个路由机制"）

### 流程

#### Step 1：初判（极简、无语义分析）

只看硬信号，不做复杂本地语义判断：
- `text_chars`（文本长度）
- `image_count`（图片数量）
- `has_tools`（是否启用工具）
- `iteration`（工具循环轮次）

产出 `difficulty_initial`（0~100）

#### Step 2：低难复核

如果 `difficulty_initial < threshold`（建议 45）：
- 调一次 GLM-4-Flash 分类器
- 输出 `difficulty_final` 和 `lane_final`
- 覆盖初判结果

否则：
- `difficulty_final = difficulty_initial`
- `lane_final = lane_initial`

### 为什么放 Router 而不是 ZeroClaw

1. Router 才是最终决策点（复核结果直接用于选模型）
2. 可以统一服务多个入口（不绑死 ZeroClaw）
3. ZeroClaw 保持轻，避免策略膨胀

---

## 启发式路由曲线

### 经验规律

基于实践经验和部分研究支持，模型强度需求随上下文长度呈"U型曲线"：

#### Phase A：开局强模型（ctx_tokens <= 8k）

- 任务开始阶段，需要强模型定结构、定方向、定角色
- 优先 GLM-4.7-Flash（而非 Gemini）
- 原因：前文质量会影响后续输出，开局产出"稳定、可验证、结构化"的内容很重要

#### Phase B：执行区弱模型（8k < ctx_tokens <= 48k）

- 任务进入执行/展开期，可用较弱模型
- GLM-4-Flash / Z1-Flash 为主
- 前提：前文已经稳定，没有错误假设污染

#### Phase C：长上下文压力区（ctx_tokens > 48k）

- 上下文越来越长，阅读理解负担增加
- 需要重新升回更强模型
- 长上下文模型优先（GLM-4.7-Flash）

### 实现建议

```python
def get_strength_floor(ctx_tokens):
    if ctx_tokens <= 8000:
        return 70  # 强模型优先
    elif ctx_tokens <= 48000:
        return 40  # 弱模型可承担
    else:
        return 65  # 长上下文压力区
```

### 注意事项

- 这个曲线是经验性的，不是实验定论
- 研究表明：前文"强不强"不如"前文是否稳定、可验证、结构化"重要
- 多轮对话存在历史污染风险，错误的前文会拖累后续表现

---

## ZeroClaw enriched 机制

### 什么是 enriched

ZeroClaw 在 `turn()` 里会先从 memory 检索相关内容，然后把它拼到用户消息前面，形成 `enriched`，再发给模型。

实际发给模型的是：
```
[Memory context]
- key1: content1
- key2: content2

用户原始消息
```

### 对难度判断的影响

难度/成本判断不能只看用户原话，应该优先看：
- `enriched` 长度（最终实际输入长度）
- `context` 是否存在（上下文依赖任务更复杂）

有些看起来很短的问题，实际 prompt 会很长（因为前面拼了记忆），这会影响：
- 上下文长度估算
- 成本估算
- 该不该用长上下文模型
- 难度评分

### 建议

Router 在做难度判断时，应该使用 ZeroClaw 传来的 `enriched` 长度，而不是自己从请求体估算。

---

## 付费模型调用策略

### 核心原则

**付费模型不因为"免费模型阻塞/忙"而触发。**

付费模型只在两种情况触发：
1. **难度达到阈值**（值得升级）
2. **免费池里没有满足要求的模型**（能力缺口，不是拥塞缺口）

### 免费池策略：吞吐最大化

- 优先级高
- 多试几次没关系
- 更积极 fallback
- 追求"能跑就行"

### 付费池策略：价值最大化

- 只有必要时才用
- 先过预算门
- 重试更谨慎（最多 1~2 次）
- 按 lane 精准补位，不选最贵的

---

## 与 ZeroClaw 的衔接

最终会有三层：

- **NapCat / OneBot Channel（ZeroClaw）**：负责 QQ 输入输出
- **ZeroClaw Agent Runtime**：负责工具/记忆/执行
- **FreePool Router（外部）**：负责 GLM 多账号多模型调度

这三个分层非常干净，也方便后面再缝：

- 麦麦式时序（放 channel/hook）
- VCP/Mem0 记忆（放 memory/provider 或外部服务）
- 模型路由（放 router）
