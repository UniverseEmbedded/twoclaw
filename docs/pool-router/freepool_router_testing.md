# FreePool Router - 测试策略

## 概述

测试 Router 时，目标是**先把路由逻辑测通，再接真实模型**。核心思路是把 Router 分成两层：

1. **路由决策层**（纯逻辑）
2. **模型执行层**（真实调用 provider）

测试时只跑第 1 层，或者用"假执行器"替换第 2 层。

---

## 核心原则

**不调用任何真实模型，尤其不碰付费模型。**

---

## dry-run 模式

### 开关

```
FREEPOOL_DRY_RUN=1
```

### 行为

开启后：
- 不调用任何真实模型
- 只返回"本来会选哪个池/哪个模型/哪个账号"的结果

### 返回内容示例

```json
{
  "selected_pool": "glm_free",
  "selected_model": "glm-4-flash",
  "selected_account": "key_2",
  "difficulty_initial": 22,
  "difficulty_final": 68,
  "reclassified_by": "glm4flash_stub",
  "reason": "low_difficulty_recheck",
  "content": "[DRY_RUN] would route to glm-4-flash (account key_2), difficulty=68"
}
```

这样 ZeroClaw 可以端到端跑起来（从用户消息到 Router 返回），能直接看日志和行为。

---

## 可插拔组件

### 1) DifficultyClassifier（难度分类器）

把"调用 GLM4Flash"抽象成接口：

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class ClassifyResult:
    lane: str
    difficulty: int
    reason: str = ""

class DifficultyClassifier(ABC):
    @abstractmethod
    def classify(self, req: dict) -> ClassifyResult:
        pass

class RealDifficultyClassifier(DifficultyClassifier):
    """真调 GLM4Flash（上线用）"""
    def __init__(self, client_pool):
        self.client_pool = client_pool
    
    def classify(self, req: dict) -> ClassifyResult:
        # 实际调用 GLM4Flash
        pass

class StubDifficultyClassifier(DifficultyClassifier):
    """不调模型，按固定规则返回（测试用）"""
    def __init__(self, rules=None):
        self.rules = rules or {}
    
    def classify(self, req: dict) -> ClassifyResult:
        # 按规则返回假结果
        if "代码" in req.get("text", ""):
            return ClassifyResult(lane="reasoning", difficulty=70)
        return ClassifyResult(lane="text_fast", difficulty=30)
```

### 2) ProviderAdapter（模型调用适配器）

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, AsyncIterator

@dataclass
class ChatResponse:
    content: str
    model: str
    usage: dict
    finish_reason: str = "stop"

class ProviderAdapter(ABC):
    @abstractmethod
    async def chat(self, messages: list, model: str, **kwargs) -> ChatResponse:
        pass
    
    @abstractmethod
    async def stream(self, messages: list, model: str, **kwargs) -> AsyncIterator[str]:
        pass

class RealProviderAdapter(ProviderAdapter):
    """真实调用 GLM/Gemini"""
    def __init__(self, client_pool):
        self.client_pool = client_pool
    
    async def chat(self, messages: list, model: str, **kwargs) -> ChatResponse:
        # 实际调用 API
        pass
    
    async def stream(self, messages: list, model: str, **kwargs) -> AsyncIterator[str]:
        # 实际流式调用
        pass

class MockProviderAdapter(ProviderAdapter):
    """假响应（测试用）"""
    def __init__(self, config: dict):
        """
        config 示例:
        {
            "gemini-2.5-pro": {"mode": "success", "latency": 0.5},
            "glm-4.7-flash": {"mode": "success", "latency": 1.0},
            "glm-4-flash": {"mode": "success", "latency": 0.1},
            "veo-3": {"mode": "disabled"},
        }
        """
        self.config = config
    
    async def chat(self, messages: list, model: str, **kwargs) -> ChatResponse:
        cfg = self.config.get(model, {"mode": "success"})
        
        if cfg["mode"] == "disabled":
            raise Exception(f"Model {model} is disabled")
        
        if cfg["mode"] == "rate_limit":
            raise Exception("Rate limit exceeded (429)")
        
        if cfg["mode"] == "fail":
            raise Exception("Mock failure")
        
        # 模拟延迟
        import asyncio
        await asyncio.sleep(cfg.get("latency", 0.1))
        
        return ChatResponse(
            content=f"[MOCK] Response from {model}",
            model=model,
            usage={"prompt_tokens": 100, "completion_tokens": 50}
        )
    
    async def stream(self, messages: list, model: str, **kwargs) -> AsyncIterator[str]:
        cfg = self.config.get(model, {"mode": "success"})
        
        if cfg["mode"] != "success":
            raise Exception(f"Mock error for {model}")
        
        for chunk in ["[MOCK] ", "Streaming ", "from ", model]:
            yield chunk
```

---

## 开发期模式开关

| 开关 | 说明 |
|------|------|
| `FREEPOOL_DRY_RUN=1` | 不实际调用模型，只返回路由决策 |
| `FREEPOOL_CLASSIFIER_MODE=stub\|real` | 低难复核用假分类器还是真 GLM4Flash |
| `FREEPOOL_PROVIDER_MODE=mock\|real` | Provider adapter 用 mock 还是真实 API |

### 组合使用

| 组合 | 用途 | 成本 |
|------|------|------|
| `dry-run + stub + mock` | 纯逻辑测试 | 0 |
| `real-run + stub + mock` | 测 Router 执行流程 | 0 |
| `real-run + real-classifier + mock` | 只测低难复核真实效果 | 低 |
| `real-run + real + real` | 上线前验证 | 正常 |

---

## 测试分层

### 第一层：纯函数测试（完全不联网）

测试：
- 初判 difficulty（长度/图片/工具）
- "低难复核触发条件"
- 路由评分函数
- 池选择规则

这层最快、最稳。

### 第二层：Router 集成测试（HTTP层，但全 Stub）

起 FastAPI 服务，但：
- classifier 用 stub
- provider 用 mock

用 `curl`/pytest 打请求，验证返回的 debug 路由结果。

这层可以测 header 解析和 OpenAI-compatible 接口。

### 第三层：影子测试（可选，少量真实）

等前两层都稳了，再做极少量真实调用验证：
- 只开 GLM4Flash（便宜）
- Gemini 先不开
- 或 Gemini 只开一个便宜模型、低并发

这时候才开始花钱，而且花得很少。

---

## 重点测试项

### A. 路由选择

- `free_only` 是否绝不走 Gemini
- `prefer_free_then_credit` 是否先选 GLM
- `premium_only` 是否直走 Gemini

### B. 低难复核流程

- 初判低难 → 是否触发分类器
- 分类器把难度提上去 → 是否改路由
- 初判高难 → 是否跳过复核

### C. 熔断/冷却

- 某账号连续失败 → 是否进入 cooldown
- cooldown 中是否不再被选中
- cooldown 到期后是否恢复

### D. Fallback

- 主选失败后是否切备选
- 是否避免重复选同一个坏 endpoint

### E. 启发式曲线

- ctx_tokens <= 8k → 是否优先强模型
- 8k < ctx_tokens <= 48k → 是否可用弱模型
- ctx_tokens > 48k → 是否升回强模型

### F. 付费模型触发

- 免费池拥塞 → 是否不触发付费
- 难度达到阈值 → 是否触发付费
- 免费池无能力 → 是否触发付费

---

## 测试用例示例

```python
import pytest

def test_low_difficulty_recheck():
    """初判低难应该触发复核"""
    router = Router(
        classifier=StubDifficultyClassifier(),
        provider=MockProviderAdapter({"glm-4-flash": {"mode": "success"}}),
        dry_run=True
    )
    
    # 短文本、无图、无工具 → 初判低难
    result = router.route({
        "messages": [{"role": "user", "content": "证明这个算法是对的"}],
        "headers": {"x-freepool-iteration": "0"}
    })
    
    # 应该触发复核
    assert result["reclassified"] == True
    assert result["difficulty_final"] > 45

def test_free_only_never_uses_gemini():
    """free_only 不应该走 Gemini"""
    router = Router(
        classifier=StubDifficultyClassifier(),
        provider=MockProviderAdapter({
            "glm-4-flash": {"mode": "rate_limit"},  # 模拟限流
            "gemini-2.5-flash": {"mode": "success"}
        }),
        dry_run=True
    )
    
    result = router.route({
        "messages": [{"role": "user", "content": "hello"}],
        "headers": {"x-freepool-budget-policy": "free_only"}
    })
    
    # 即使 GLM 限流，也不应该选 Gemini
    assert result["selected_pool"] != "gemini"

def test_heuristic_curve():
    """启发式曲线应该影响模型选择"""
    router = Router(dry_run=True)
    
    # 短上下文 → 强模型优先
    result_short = router.get_strength_floor(4000)
    assert result_short >= 70
    
    # 中等上下文 → 弱模型可承担
    result_mid = router.get_strength_floor(24000)
    assert result_mid <= 50
    
    # 长上下文 → 强模型
    result_long = router.get_strength_floor(80000)
    assert result_long >= 60
```

---

## 避免递归

如果用 GLM-4-Flash 来给请求分类，Router 要避免"分类请求自己又走分类器"：

```python
def classify(self, req: dict) -> ClassifyResult:
    # 给分类请求打内部标记
    internal_req = {**req, "_internal_classification": True}
    
    # 强制它直连固定 endpoint
    # 禁止再进入路由分类逻辑
    ...
```

不然会递归套娃。

---

## 一句话总结

**测试 Router 时，把"分类器"和"模型调用"都抽象成可替换组件；默认用 stub/mock，开启 dry-run 返回路由决策，不实际调用任何模型（尤其不碰付费模型）。**
