import asyncio
import base64
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Mapping, MutableMapping, Optional

from .pricing import estimate_cost_usd


def _now_s() -> float:
    return time.time()


def _clamp_int(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


MAX_DIFFICULTY = 100
LEGACY_MAX_DIFFICULTY = 65535


def _normalize_difficulty(v: int) -> int:
    iv = int(v)
    if iv <= MAX_DIFFICULTY:
        return _clamp_int(iv, 0, MAX_DIFFICULTY)
    mapped = int(round(iv * MAX_DIFFICULTY / float(LEGACY_MAX_DIFFICULTY)))
    return _clamp_int(mapped, 0, MAX_DIFFICULTY)


def _get_header(headers: Mapping[str, str], name: str) -> Optional[str]:
    for k, v in headers.items():
        if k.lower() == name.lower():
            return v
    return None


def _estimate_tokens_from_text(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


def _extract_text_and_images_from_messages(messages: list[dict[str, Any]]) -> tuple[str, int]:
    text_parts: list[str] = []
    image_count = 0
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            text_parts.append(content)
            continue
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                ptype = part.get("type")
                if ptype == "text" and isinstance(part.get("text"), str):
                    text_parts.append(part["text"])
                elif ptype == "image_url":
                    image_count += 1
    return "\n".join(text_parts), image_count


def _parse_bool01(v: Optional[str]) -> Optional[bool]:
    if v is None:
        return None
    vv = v.strip().lower()
    if vv in {"1", "true", "yes", "y"}:
        return True
    if vv in {"0", "false", "no", "n"}:
        return False
    return None


def _parse_int(v: Optional[str]) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v.strip())
    except Exception:
        return None


def _parse_float(v: Optional[str]) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v.strip())
    except Exception:
        return None


def _test_mode_level() -> int:
    raw = (os.environ.get("FREEPOOL_TEST_MODE") or "").strip()
    if not raw:
        return 0
    try:
        return max(0, int(raw))
    except Exception:
        return 1


def _truncate_str(v: Optional[str], max_len: int) -> Optional[str]:
    if v is None:
        return None
    if not isinstance(v, str):
        return str(v)
    if len(v) <= max_len:
        return v
    return v[:max_len] + f"...(truncated,len={len(v)})"


def _request_summary(*, headers: Mapping[str, str], request_body: Mapping[str, Any]) -> dict[str, Any]:
    model = request_body.get("model")
    msgs = request_body.get("messages")
    msg_count = len(msgs) if isinstance(msgs, list) else 0
    roles: list[str] = []
    user_text = ""
    if isinstance(msgs, list):
        for m in msgs:
            if not isinstance(m, dict):
                continue
            r = m.get("role")
            if isinstance(r, str):
                roles.append(r)
    if isinstance(msgs, list):
        msg_dicts = [m for m in msgs if isinstance(m, dict)]
        user_msgs = [m for m in msg_dicts if m.get("role") == "user"]
        user_text, _ = _extract_text_and_images_from_messages(user_msgs or msg_dicts)
    max_tokens = request_body.get("max_tokens")
    return {
        "model": str(model) if isinstance(model, str) else None,
        "message_count": msg_count,
        "roles_head": roles[:12],
        "user_text_head": _truncate_str(user_text, 256),
        "max_tokens": int(max_tokens) if isinstance(max_tokens, int) else None,
        "ua": _get_header(headers, "user-agent") or None,
    }


@dataclass(frozen=True)
class RouteHints:
    iteration: int = 0
    has_tools: Optional[bool] = None
    agent_phase: Optional[str] = None
    tool_error_count: int = 0
    budget_policy: str = "free_only"
    lane: Optional[str] = None
    difficulty: Optional[int] = None
    priority: str = "normal"
    requires_thinking: Optional[bool] = None
    task_profile_b64: Optional[str] = None
    max_estimated_cost_usd: Optional[float] = None
    value_tier: Optional[str] = None
    trace_id: Optional[str] = None
    scene: Optional[str] = None


@dataclass(frozen=True)
class BudgetDecision:
    allowed: bool
    hard_block: bool
    reason: str
    estimated_cost_usd: Optional[float]
    max_estimated_cost_usd: Optional[float]
    value_tier: Optional[str]


@dataclass
class QuotaBucket:
    bucket_id: str
    scope: str
    model: str
    max_inflight: int
    inflight: int = 0
    cooldown_until_s: float = 0.0
    recent_429: int = 0
    window_1h_start_s: float = 0.0
    window_1d_start_s: float = 0.0
    window_1h_cost_usd: float = 0.0
    window_1d_cost_usd: float = 0.0
    window_1h_tokens: int = 0
    window_1d_tokens: int = 0


@dataclass(frozen=True)
class RouteScoreBreakdown:
    total: float
    capability: float
    congestion: float
    cost_penalty: float
    risk_penalty: float
    policy_bonus: float
    reasons: list[str]


@dataclass(frozen=True)
class TaskProfile:
    modality: str
    prompt_tokens_est: int
    requested_output_tokens: int
    has_tools: bool
    requires_json: bool
    requires_thinking: bool
    conversation_turns: int
    image_count: int
    is_coding: bool
    is_math_logic: bool
    is_longform: bool
    ctx_tokens_est: int


@dataclass(frozen=True)
class ClassifyResult:
    lane: str
    difficulty: int
    reason: str = ""


class DifficultyClassifier:
    async def classify(self, request_body: Mapping[str, Any], profile: TaskProfile) -> ClassifyResult:
        raise NotImplementedError


class ClassificationError(Exception):
    def __init__(
        self,
        message: str,
        *,
        kind: str,
        account_id: Optional[str] = None,
        raw: Optional[str] = None,
    ):
        super().__init__(message)
        self.kind = kind
        self.account_id = account_id
        self.raw = raw


def _try_parse_first_json_object(text: str) -> Optional[dict[str, Any]]:
    if not text:
        return None
    s = text.strip()
    if s.startswith("{") and s.endswith("}"):
        try:
            data = json.loads(s)
            return data if isinstance(data, dict) else None
        except Exception:
            return None
    start = s.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(s)):
        ch = s[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                frag = s[start : i + 1]
                try:
                    data = json.loads(frag)
                    return data if isinstance(data, dict) else None
                except Exception:
                    return None
    return None


class RealDifficultyClassifier(DifficultyClassifier):
    def __init__(
        self,
        *,
        provider: "ProviderAdapter",
        glm_account_keys: Mapping[str, str],
        model: str = "GLM-4-Flash",
    ):
        self._provider = provider
        self._glm_accounts = dict(glm_account_keys)
        self._model = model
        self._rr_index = 0
        self._cooldown_until_s: dict[str, float] = {}
        self._lock = asyncio.Lock()

    def _pick_account_id(self) -> Optional[str]:
        for k in self._glm_accounts.keys():
            if isinstance(k, str) and k:
                return k
        return None

    def _account_ids(self) -> list[str]:
        out: list[str] = []
        for k in self._glm_accounts.keys():
            if isinstance(k, str) and k:
                out.append(k)
        return out

    async def _candidate_accounts(self) -> list[str]:
        accounts = self._account_ids()
        if not accounts:
            return []
        async with self._lock:
            i = self._rr_index % max(1, len(accounts))
            self._rr_index += 1
        return accounts[i:] + accounts[:i]

    def _build_messages(self, request_body: Mapping[str, Any], profile: TaskProfile) -> list[dict[str, Any]]:
        msgs = request_body.get("messages") or []
        if not isinstance(msgs, list):
            msgs = []
        msg_dicts = [m for m in msgs if isinstance(m, dict)]
        user_msgs = [m for m in msg_dicts if m.get("role") == "user"]
        last_user_msg = next((m for m in reversed(msg_dicts) if m.get("role") == "user"), None)
        last_user_text = ""
        if isinstance(last_user_msg, dict):
            last_user_text, _ = _extract_text_and_images_from_messages([last_user_msg])
        user_text, _ = _extract_text_and_images_from_messages(user_msgs or msg_dicts)

        system = (
            "你是一个路由难度分类器。"
            "请根据用户任务的复杂度与推理需求，返回严格 JSON 对象："
            '{"lane":"text_fast","difficulty":0-100,"reason":"..."}。'
            "lane 必须是以下之一：text_fast, reasoning, long_context, vision, tool_heavy。"
            "difficulty 越高表示越需要强推理或更高成本模型。"
            "必须以 last_user_text 为主要依据；recent_user_text 仅作为背景上下文，避免被上一轮高难度话题惯性影响。"
            "只输出 JSON，不要输出其它内容。"
            "\n\n评分规则（用于稳定一致的路由）："
            "\n- 纯寒暄/简单问候：difficulty 5-25，lane=text_fast"
            "\n- 科普解释/现象原理（不要求严谨推导）：difficulty 20-45，lane=text_fast"
            "\n- 历史/社会关系/动机分析且问题较长：difficulty 55-80，lane=reasoning"
            "\n- 思想实验/悖论/可计算性等，需要深推理：difficulty 75-95，lane=reasoning"
            "\n- 仅风格/角色/语气/格式要求（如“以猫娘身份聊天”）：difficulty 5-30，lane=text_fast"
        )
        user = json.dumps(
            {
                "last_user_text": last_user_text[-4000:],
                "recent_user_text": user_text[-8000:],
                "profile": {
                    "modality": profile.modality,
                    "prompt_tokens_est": profile.prompt_tokens_est,
                    "requested_output_tokens": profile.requested_output_tokens,
                    "has_tools": profile.has_tools,
                    "requires_json": profile.requires_json,
                    "requires_thinking": profile.requires_thinking,
                    "conversation_turns": profile.conversation_turns,
                    "image_count": profile.image_count,
                    "is_coding": profile.is_coding,
                    "is_math_logic": profile.is_math_logic,
                    "is_longform": profile.is_longform,
                    "ctx_tokens_est": profile.ctx_tokens_est,
                },
            },
            ensure_ascii=False,
        )
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    async def classify(self, request_body: Mapping[str, Any], profile: TaskProfile) -> ClassifyResult:
        candidates = await self._candidate_accounts()
        if not candidates:
            raise ClassificationError("no_glm_account", kind="no_glm_account")

        messages = self._build_messages(request_body, profile)
        req = {
            "model": self._model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": 256,
            "response_format": {"type": "json_object"},
            "_internal_classification": True,
            "stream": False,
        }
        account_id = candidates[0]
        now = _now_s()
        until = float(self._cooldown_until_s.get(account_id, 0.0))
        if until > now:
            raise ClassificationError("account_cooldown", kind="account_cooldown", account_id=account_id)

        selected = SelectedEndpoint(pool="glm_free", model=self._model, account_id=account_id)
        try:
            resp = await self._provider.chat(
                model=self._model,
                messages=messages,
                headers={},
                request_body=req,
                selected=selected,
            )
        except ProviderError as e:
            cooldown_s = 20.0 if int(e.status_code or 0) == 429 else 60.0
            self._cooldown_until_s[account_id] = _now_s() + cooldown_s
            raise ClassificationError(
                f"provider_error status_code={int(e.status_code or 0)} code={int(e.code or 0)} message={str(e)}",
                kind="provider_error",
                account_id=account_id,
            )
        except Exception as e:
            self._cooldown_until_s[account_id] = _now_s() + 20.0
            raise ClassificationError(f"exception {e.__class__.__name__}: {str(e)}", kind="exception", account_id=account_id)

        content = resp.get("content") if isinstance(resp, dict) else None
        if not isinstance(content, str) or not content.strip():
            self._cooldown_until_s[account_id] = _now_s() + 10.0
            raise ClassificationError("empty_response", kind="empty", account_id=account_id, raw=str(content))

        data = _try_parse_first_json_object(content)
        if not data:
            self._cooldown_until_s[account_id] = _now_s() + 10.0
            raise ClassificationError("parse_error", kind="parse", account_id=account_id, raw=content)

        lane = data.get("lane")
        difficulty = data.get("difficulty")
        reason = data.get("reason") or ""
        if not isinstance(lane, str):
            raise ClassificationError("schema_lane", kind="schema_lane", account_id=account_id, raw=content)
        lane = _normalize_lane_alias(lane.strip())
        if lane not in {"text_fast", "reasoning", "long_context", "vision", "tool_heavy"}:
            raise ClassificationError("schema_lane", kind="schema_lane", account_id=account_id, raw=content)
        if not isinstance(difficulty, int):
            if isinstance(difficulty, float):
                difficulty = int(difficulty)
            elif isinstance(difficulty, str) and difficulty.strip().isdigit():
                difficulty = int(difficulty.strip())
            else:
                raise ClassificationError("schema_difficulty", kind="schema_difficulty", account_id=account_id, raw=content)
        return ClassifyResult(lane=lane, difficulty=_normalize_difficulty(int(difficulty)), reason=str(reason))


@dataclass(frozen=True)
class ModelProfile:
    model: str
    pool: str
    modalities: set[str]
    max_context: int
    max_output: int
    thinking: str
    tool_call: bool
    base_concurrency: int
    reserve_for_high_difficulty: bool = False
    min_difficulty: int = 0


@dataclass
class EndpointState:
    configured_concurrency: int
    inflight: int = 0
    cooldown_until_s: float = 0.0
    recent_1302_count: int = 0
    recent_1305_count: int = 0
    success_5m: int = 0
    fail_5m: int = 0
    last_used_at_s: float = 0.0
    semaphore: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(1))


@dataclass(frozen=True)
class SelectedEndpoint:
    pool: str
    model: str
    account_id: Optional[str]


class ProviderError(Exception):
    def __init__(self, message: str, code: Optional[int] = None, status_code: Optional[int] = None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class ProviderAdapter:
    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        headers: Mapping[str, str],
        request_body: Mapping[str, Any],
        selected: SelectedEndpoint,
    ) -> dict[str, Any]:
        raise NotImplementedError

    async def stream(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        headers: Mapping[str, str],
        request_body: Mapping[str, Any],
        selected: SelectedEndpoint,
    ) -> AsyncIterator[dict[str, Any]]:
        raise NotImplementedError

    async def list_models(self) -> list[dict[str, Any]]:
        return []


class MockProviderAdapter(ProviderAdapter):
    def __init__(self, model_config: Mapping[str, Mapping[str, Any]]):
        self._cfg: dict[str, dict[str, Any]] = {k: dict(v) for k, v in model_config.items()}

    def _pick_cfg(self, *, selected: SelectedEndpoint, model: str) -> dict[str, Any]:
        account_id = selected.account_id or "__default__"
        return (
            self._cfg.get(f"{account_id}:{model}")
            or self._cfg.get(model)
            or {"mode": "success"}
        )

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        headers: Mapping[str, str],
        request_body: Mapping[str, Any],
        selected: SelectedEndpoint,
    ) -> dict[str, Any]:
        cfg = self._pick_cfg(selected=selected, model=model)
        mode = cfg.get("mode") or cfg.get("type") or "success"
        if mode == "disabled":
            raise ProviderError(str(cfg.get("message") or f"Model {model} is disabled"), status_code=int(cfg.get("status_code") or 400))
        if mode == "rate_limit":
            raise ProviderError(
                str(cfg.get("message") or "rate limited"),
                code=int(cfg.get("code") or 1305),
                status_code=int(cfg.get("status_code") or 429),
            )
        if mode == "account_rate_limit":
            raise ProviderError(
                str(cfg.get("message") or "account rate limited"),
                code=int(cfg.get("code") or 1302),
                status_code=int(cfg.get("status_code") or 429),
            )
        if mode == "fail":
            raise ProviderError(
                str(cfg.get("message") or "mock failure"),
                code=int(cfg["code"]) if isinstance(cfg.get("code"), int) else None,
                status_code=int(cfg.get("status_code") or 500),
            )
        await asyncio.sleep(float(cfg.get("latency", 0.01)))
        content = cfg.get("content")
        if not isinstance(content, str):
            content = f"[MOCK] Response from {model}"
        usage = cfg.get("usage")
        if not isinstance(usage, dict):
            usage = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
        finish_reason = cfg.get("finish_reason")
        if not isinstance(finish_reason, str):
            finish_reason = "stop"
        return {
            "content": content,
            "usage": usage,
            "finish_reason": finish_reason,
        }

    async def stream(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        headers: Mapping[str, str],
        request_body: Mapping[str, Any],
        selected: SelectedEndpoint,
    ) -> AsyncIterator[dict[str, Any]]:
        cfg = self._pick_cfg(selected=selected, model=model)
        mode = cfg.get("mode") or cfg.get("type") or "success"
        if mode != "success":
            raise ProviderError(
                str(cfg.get("message") or "mock stream error"),
                code=int(cfg["code"]) if isinstance(cfg.get("code"), int) else None,
                status_code=int(cfg.get("status_code") or 500),
            )
        chunks = cfg.get("chunks")
        if not isinstance(chunks, list) or not all(isinstance(c, str) for c in chunks):
            chunks = ["[MOCK] ", "Streaming ", "from ", model]
        for chunk in chunks:
            await asyncio.sleep(float(cfg.get("latency", 0.01)))
            yield {"delta": chunk}
        finish_reason = cfg.get("finish_reason")
        if not isinstance(finish_reason, str):
            finish_reason = "stop"
        yield {"finish_reason": finish_reason}


def _default_model_registry() -> dict[str, ModelProfile]:
    return {
        "GLM-4-Flash": ModelProfile(
            model="GLM-4-Flash",
            pool="glm_free",
            modalities={"text"},
            max_context=32000,
            max_output=8192,
            thinking="toggle",
            tool_call=True,
            base_concurrency=30,
        ),
        "GLM-4-Flash-250414": ModelProfile(
            model="GLM-4-Flash-250414",
            pool="glm_free",
            modalities={"text"},
            max_context=64000,
            max_output=8192,
            thinking="toggle",
            tool_call=True,
            base_concurrency=16,
        ),
        "GLM-Z1-Flash": ModelProfile(
            model="GLM-Z1-Flash",
            pool="glm_free",
            modalities={"text"},
            max_context=32000,
            max_output=32000,
            thinking="forced",
            tool_call=False,
            base_concurrency=30,
        ),
        "GLM-4.7-Flash": ModelProfile(
            model="GLM-4.7-Flash",
            pool="glm_free",
            modalities={"text"},
            max_context=200000,
            max_output=128000,
            thinking="toggle",
            tool_call=True,
            base_concurrency=1,
            reserve_for_high_difficulty=True,
            min_difficulty=55,
        ),
        "GLM-4V-Flash": ModelProfile(
            model="GLM-4V-Flash",
            pool="glm_free",
            modalities={"text", "image"},
            max_context=32000,
            max_output=8192,
            thinking="toggle",
            tool_call=True,
            base_concurrency=2,
        ),
        "GLM-4.1V-Thinking-Flash": ModelProfile(
            model="GLM-4.1V-Thinking-Flash",
            pool="glm_free",
            modalities={"text", "image"},
            max_context=64000,
            max_output=8192,
            thinking="forced",
            tool_call=True,
            base_concurrency=1,
        ),
        "GLM-4.6V-Flash": ModelProfile(
            model="GLM-4.6V-Flash",
            pool="glm_free",
            modalities={"text", "image", "file", "video"},
            max_context=128000,
            max_output=32000,
            thinking="toggle",
            tool_call=True,
            base_concurrency=1,
        ),
        "gemini-2.5-flash": ModelProfile(
            model="gemini-2.5-flash",
            pool="gemini_credit",
            modalities={"text", "image"},
            max_context=1048576,
            max_output=8192,
            thinking="toggle",
            tool_call=False,
            base_concurrency=8,
        ),
        "gemini-3-flash-preview": ModelProfile(
            model="gemini-3-flash-preview",
            pool="gemini_credit",
            modalities={"text", "image"},
            max_context=1048576,
            max_output=8192,
            thinking="toggle",
            tool_call=False,
            base_concurrency=4,
        ),
    }


def parse_route_hints(headers: Mapping[str, str], request_body: Mapping[str, Any]) -> RouteHints:
    body_ext = request_body.get("_freepool") if isinstance(request_body.get("_freepool"), dict) else {}

    iteration = _parse_int(_get_header(headers, "x-freepool-iteration")) or 0
    has_tools = _parse_bool01(_get_header(headers, "x-freepool-has-tools"))
    agent_phase = _get_header(headers, "x-freepool-agent-phase")
    tool_error_count = _parse_int(_get_header(headers, "x-freepool-tool-error-count")) or 0
    budget_policy = (
        _get_header(headers, "x-freepool-budget-policy")
        or body_ext.get("budget_policy")
        or "free_only"
    )
    lane = _get_header(headers, "x-freepool-lane")
    if lane is None:
        lane = body_ext.get("lane")

    hdr_difficulty = _parse_int(_get_header(headers, "x-freepool-difficulty"))
    if hdr_difficulty is not None:
        difficulty = hdr_difficulty
    else:
        body_difficulty = body_ext.get("difficulty")
        if isinstance(body_difficulty, int):
            difficulty = int(body_difficulty)
        elif isinstance(body_difficulty, float):
            difficulty = int(body_difficulty)
        elif isinstance(body_difficulty, str):
            difficulty = _parse_int(body_difficulty)
        else:
            difficulty = None
    priority = _get_header(headers, "x-freepool-priority") or body_ext.get("priority") or "normal"
    requires_thinking = _parse_bool01(_get_header(headers, "x-freepool-requires-thinking"))
    task_profile_b64 = _get_header(headers, "x-freepool-task-profile")
    max_estimated_cost_usd = _parse_float(_get_header(headers, "x-freepool-max-estimated-cost-usd"))
    value_tier = _get_header(headers, "x-freepool-value-tier")
    trace_id = _get_header(headers, "x-freepool-trace-id")
    scene = _get_header(headers, "x-freepool-scene")

    return RouteHints(
        iteration=iteration,
        has_tools=has_tools,
        agent_phase=agent_phase,
        tool_error_count=tool_error_count,
        budget_policy=str(budget_policy),
        lane=str(lane) if lane is not None else None,
        difficulty=_normalize_difficulty(difficulty) if isinstance(difficulty, int) else None,
        priority=str(priority),
        requires_thinking=requires_thinking,
        task_profile_b64=str(task_profile_b64) if task_profile_b64 is not None else None,
        max_estimated_cost_usd=max_estimated_cost_usd,
        value_tier=str(value_tier) if value_tier is not None else None,
        trace_id=str(trace_id) if trace_id is not None else None,
        scene=str(scene) if scene is not None else None,
    )


def build_task_profile(request_body: Mapping[str, Any], hints: RouteHints) -> TaskProfile:
    messages = request_body.get("messages") or []
    if not isinstance(messages, list):
        messages = []

    msg_dicts = [m for m in messages if isinstance(m, dict)]
    user_msgs = [m for m in msg_dicts if m.get("role") == "user"]

    text_all, image_count = _extract_text_and_images_from_messages(msg_dicts)
    text_user, image_user = _extract_text_and_images_from_messages(user_msgs)
    prompt_tokens_est = _estimate_tokens_from_text(text_all) + image_count * 1000
    ctx_tokens_est = _estimate_tokens_from_text(text_all) + image_count * 1000
    max_tokens = request_body.get("max_tokens") or request_body.get("max_completion_tokens") or 0
    requested_output_tokens = int(max_tokens) if isinstance(max_tokens, int) else 0

    tool_choice = request_body.get("tool_choice")
    if hints.has_tools is not None:
        has_tools = bool(hints.has_tools)
    else:
        if isinstance(tool_choice, dict):
            has_tools = True
        elif isinstance(tool_choice, str) and tool_choice.strip().lower() == "required":
            has_tools = True
        else:
            has_tools = False
    response_format = request_body.get("response_format") if isinstance(request_body.get("response_format"), dict) else {}
    requires_json = response_format.get("type") in {"json_object", "json_schema"}
    requires_thinking = bool(hints.requires_thinking) if hints.requires_thinking is not None else False

    conversation_turns = len(messages)
    is_coding = any(k in text_user for k in ["代码", "编译", "报错", "单元测试", "重构"]) or any(
        k in text_user.lower() for k in ["code", "compile", "stack trace", "typescript", "python", "rust"]
    )
    is_math_logic = any(k in text_user for k in ["证明", "推导", "复杂度", "不变量", "正确性"]) or any(
        k in text_user.lower() for k in ["prove", "derivation", "complexity", "invariant", "correctness"]
    )
    is_longform = requested_output_tokens >= 4096 or ctx_tokens_est >= 24000

    modality = "vision" if image_count > 0 else "text"
    if hints.task_profile_b64:
        dec = _decode_task_profile_b64(hints.task_profile_b64)
        if isinstance(dec, dict):
            pt = dec.get("prompt_tokens_est")
            if isinstance(pt, int):
                prompt_tokens_est = _clamp_int(int(pt), 0, 10_000_000)
            ct = dec.get("ctx_tokens_est")
            if isinstance(ct, int):
                ctx_tokens_est = _clamp_int(int(ct), 0, 10_000_000)
            it = dec.get("image_count")
            if isinstance(it, int):
                image_count = _clamp_int(int(it), 0, 64)
            mt = dec.get("modality")
            if isinstance(mt, str) and mt.strip().lower() in {"text", "vision"}:
                modality = mt.strip().lower()
            ro = dec.get("requested_output_tokens")
            if isinstance(ro, int):
                requested_output_tokens = _clamp_int(int(ro), 0, 1_000_000)
            ht = dec.get("has_tools")
            if isinstance(ht, bool):
                has_tools = bool(ht)
            rj = dec.get("requires_json")
            if isinstance(rj, bool):
                requires_json = bool(rj)
            rt = dec.get("requires_thinking")
            if isinstance(rt, bool):
                requires_thinking = bool(rt)
            turns = dec.get("conversation_turns")
            if isinstance(turns, int):
                conversation_turns = _clamp_int(int(turns), 0, 1024)
    return TaskProfile(
        modality=modality,
        prompt_tokens_est=prompt_tokens_est,
        requested_output_tokens=requested_output_tokens,
        has_tools=has_tools,
        requires_json=requires_json,
        requires_thinking=requires_thinking,
        conversation_turns=conversation_turns,
        image_count=image_count,
        is_coding=is_coding,
        is_math_logic=is_math_logic,
        is_longform=is_longform,
        ctx_tokens_est=ctx_tokens_est,
    )


def estimate_initial_difficulty(profile: TaskProfile) -> int:
    score = 25
    if profile.modality == "vision":
        score += 10
    if profile.prompt_tokens_est > 32000:
        score += 20
    if profile.prompt_tokens_est > 64000:
        score += 15
    if profile.has_tools:
        score += 15
    if profile.requires_json:
        score += 10
    if profile.is_coding:
        score += 15
    if profile.is_math_logic:
        score += 20
    if profile.image_count >= 2:
        score += 10
    if profile.requested_output_tokens > 8000:
        score += 10
    if not profile.has_tools and profile.image_count == 0 and profile.prompt_tokens_est < 1200:
        score -= 20
    if profile.conversation_turns <= 2 and profile.prompt_tokens_est < 800:
        score -= 15
    return _clamp_int(score, 0, 100)


def choose_lane(hints: RouteHints, profile: TaskProfile, difficulty: int) -> str:
    if hints.lane:
        return _normalize_lane_alias(hints.lane)
    if profile.modality == "vision":
        return "vision"
    if profile.ctx_tokens_est > 48000:
        return "long_context"
    if profile.has_tools:
        return "tool_heavy"
    if profile.is_coding or profile.is_math_logic or difficulty >= 55:
        return "reasoning"
    return "text_fast"


def _normalize_lane_alias(v: str) -> str:
    s = (v or "").strip()
    if not s:
        return ""
    ss = s.strip().lower()
    if "|" in ss:
        parts = [p.strip() for p in ss.split("|") if p.strip()]
        if parts:
            pref = {"tool_heavy": 0, "vision": 1, "long_context": 2, "reasoning": 3, "text_fast": 4}
            best: tuple[int, str] | None = None
            for p in parts:
                n = _normalize_lane_alias(p)
                if n in pref:
                    cand = (pref[n], n)
                    if best is None or cand < best:
                        best = cand
            if best is not None:
                return best[1]
    if "/" in ss:
        ss = ss.split("/")[-1].strip()
    aliases: dict[str, str] = {
        "chat": "text_fast",
        "general": "text_fast",
        "text": "text_fast",
        "tool": "tool_heavy",
        "tools": "tool_heavy",
        "tool_use": "tool_heavy",
        "tool-heavy": "tool_heavy",
        "long": "long_context",
        "ctx_long": "long_context",
    }
    return aliases.get(ss, ss)


def _strength_floor(ctx_tokens: int) -> int:
    if ctx_tokens <= 8000:
        return 70
    if ctx_tokens <= 48000:
        return 40
    return 65


def _glm_models_for_lane(lane: str, difficulty: int, profile: TaskProfile) -> list[str]:
    if lane in {"vision"}:
        if profile.image_count <= 1 and difficulty <= 55:
            return ["GLM-4V-Flash", "GLM-4.1V-Thinking-Flash", "GLM-4.6V-Flash"]
        if difficulty >= 70 or profile.image_count >= 2:
            return ["GLM-4.1V-Thinking-Flash", "GLM-4.6V-Flash", "GLM-4V-Flash"]
        return ["GLM-4.6V-Flash", "GLM-4.1V-Thinking-Flash", "GLM-4V-Flash"]

    if lane in {"long_context"}:
        return ["GLM-4.7-Flash", "GLM-4-Flash-250414", "GLM-4-Flash"]

    strength = _strength_floor(profile.ctx_tokens_est)
    if difficulty < 25:
        return ["GLM-4-Flash", "GLM-4-Flash-250414", "GLM-4.7-Flash"] if strength < 70 else [
            "GLM-4.7-Flash",
            "GLM-4-Flash-250414",
            "GLM-4-Flash",
        ]
    if difficulty <= 60:
        if profile.is_coding or profile.is_math_logic or lane in {"reasoning", "tool_heavy"}:
            return ["GLM-Z1-Flash", "GLM-4-Flash-250414", "GLM-4.7-Flash", "GLM-4-Flash"]
        return ["GLM-4-Flash-250414", "GLM-4-Flash", "GLM-4.7-Flash"]
    if profile.is_math_logic and profile.ctx_tokens_est <= 32000:
        return ["GLM-Z1-Flash", "GLM-4.7-Flash", "GLM-4-Flash-250414"]
    return ["GLM-4.7-Flash", "GLM-4-Flash-250414", "GLM-4-Flash"]


def _gemini_models_for_lane(lane: str, profile: TaskProfile) -> list[str]:
    if lane in {"vision", "reasoning", "tool_heavy", "long_context"}:
        return ["gemini-3-flash-preview", "gemini-2.5-flash"]
    return ["gemini-2.5-flash", "gemini-3-flash-preview"]


def _decode_task_profile_b64(v: str) -> Optional[dict[str, Any]]:
    try:
        raw = base64.b64decode(v)
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


class Router:
    def __init__(
        self,
        *,
        provider: ProviderAdapter,
        classifier: DifficultyClassifier,
        model_registry: Optional[Mapping[str, ModelProfile]] = None,
        dry_run: bool = False,
        glm_account_keys: Optional[Mapping[str, str]] = None,
        router_auth_key: Optional[str] = None,
    ):
        self._provider = provider
        self._classifier = classifier
        self._models = dict(model_registry or _default_model_registry())
        self._dry_run = dry_run
        self._router_auth_key = router_auth_key
        if isinstance(glm_account_keys, dict):
            self._glm_accounts = glm_account_keys
        else:
            self._glm_accounts = dict(glm_account_keys or {})
        self._endpoint_states: MutableMapping[tuple[str, str, str], EndpointState] = {}
        self._model_cooldown_until_s: MutableMapping[str, float] = {}
        self._account_cooldown_until_s: MutableMapping[str, float] = {}
        self._quota_buckets: MutableMapping[str, QuotaBucket] = {}
        self._gemini_project = (
            (os.environ.get("FREEPOOL_GEMINI_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT") or "").strip()
        )
        self._gemini_bucket_max_inflight = _clamp_int(int(os.environ.get("FREEPOOL_GEMINI_MAX_INFLIGHT") or "2"), 1, 32)
        self._gemini_hourly_cap_usd = _parse_float(os.environ.get("FREEPOOL_GEMINI_HOURLY_CAP_USD"))
        self._gemini_daily_cap_usd = _parse_float(os.environ.get("FREEPOOL_GEMINI_DAILY_CAP_USD"))
        self._lock = asyncio.Lock()

    @staticmethod
    def from_env(provider: ProviderAdapter, classifier: DifficultyClassifier) -> "Router":
        dry_run = os.environ.get("FREEPOOL_DRY_RUN", "").strip() == "1"
        router_auth_key = os.environ.get("FREEPOOL_ROUTER_API_KEY") or None
        keys_raw = os.environ.get("FREEPOOL_GLM_KEYS", "") or ""
        glm_keys: dict[str, str] = {}
        for i, key in enumerate([k.strip() for k in keys_raw.split(",") if k.strip()]):
            glm_keys[f"key_{i+1}"] = key
        return Router(
            provider=provider,
            classifier=classifier,
            dry_run=dry_run,
            glm_account_keys=glm_keys,
            router_auth_key=router_auth_key,
        )

    def _auth_ok(self, headers: Mapping[str, str]) -> bool:
        if not self._router_auth_key:
            return True
        auth = _get_header(headers, "authorization") or ""
        parts = auth.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1] == self._router_auth_key
        return False

    async def list_models(self) -> list[dict[str, Any]]:
        models = [{"id": "auto", "object": "model"}]
        for m in self._models.values():
            models.append({"id": m.model, "object": "model"})
        try:
            upstream = await self._provider.list_models()
            if isinstance(upstream, list):
                for it in upstream:
                    if isinstance(it, dict) and isinstance(it.get("id"), str):
                        models.append({"id": it["id"], "object": "model"})
        except Exception:
            pass
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for it in models:
            mid = it.get("id")
            if not isinstance(mid, str) or not mid:
                continue
            if mid in seen:
                continue
            seen.add(mid)
            out.append(it)
        return out

    def _normalize_budget_policy(self, v: str) -> str:
        vv = (v or "").strip().lower()
        if vv in {"free_only", "prefer_free_then_credit", "credit_allowed", "premium_only"}:
            return vv
        return "free_only"

    def _budget_cap_usd(self, *, hints: RouteHints) -> Optional[float]:
        if hints.max_estimated_cost_usd is not None:
            return float(hints.max_estimated_cost_usd)
        tier = (hints.value_tier or "").strip().lower()
        if tier == "low":
            return 0.01
        if tier == "normal":
            return 0.03
        if tier == "high":
            return 0.1
        return None

    def _estimate_credit_cost_usd(self, *, model: str, profile: TaskProfile) -> Optional[float]:
        prompt_tokens = int(profile.ctx_tokens_est)
        completion_tokens = int(profile.requested_output_tokens if profile.requested_output_tokens > 0 else 1024)
        return estimate_cost_usd(model=model, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)

    async def _budget_decision_for_credit(
        self,
        *,
        hints: RouteHints,
        profile: TaskProfile,
        lane: str,
        difficulty: int,
    ) -> tuple[BudgetDecision, dict[str, Optional[float]], set[str]]:
        cap = self._budget_cap_usd(hints=hints)
        budget_policy = self._normalize_budget_policy(hints.budget_policy)
        tier = (hints.value_tier or "").strip().lower() or None

        if budget_policy == "free_only":
            return (
                BudgetDecision(
                    allowed=False,
                    hard_block=True,
                    reason="free_only",
                    estimated_cost_usd=None,
                    max_estimated_cost_usd=cap,
                    value_tier=tier,
                ),
                {},
                set(),
            )

        credit_models_raw = _gemini_models_for_lane(lane, profile)
        credit_mps = [self._models[m] for m in credit_models_raw if m in self._models]
        credit_mps = [m for m in credit_mps if await self._model_hard_ok(m, profile=profile, difficulty=difficulty)]

        costs: dict[str, Optional[float]] = {}
        allowed: set[str] = set()
        min_cost: Optional[float] = None
        for mp in credit_mps:
            c = self._estimate_credit_cost_usd(model=mp.model, profile=profile)
            costs[mp.model] = c
            if cap is None:
                allowed.add(mp.model)
                if c is not None:
                    min_cost = c if min_cost is None else min(min_cost, c)
                continue
            if c is None:
                continue
            if c <= cap + 1e-9:
                allowed.add(mp.model)
                min_cost = c if min_cost is None else min(min_cost, c)

        if cap is None:
            return (
                BudgetDecision(
                    allowed=True,
                    hard_block=False,
                    reason="ok",
                    estimated_cost_usd=min_cost,
                    max_estimated_cost_usd=cap,
                    value_tier=tier,
                ),
                costs,
                allowed,
            )

        if not credit_mps:
            return (
                BudgetDecision(
                    allowed=False,
                    hard_block=False,
                    reason="no_credit_models",
                    estimated_cost_usd=min_cost,
                    max_estimated_cost_usd=cap,
                    value_tier=tier,
                ),
                costs,
                allowed,
            )

        if allowed:
            return (
                BudgetDecision(
                    allowed=True,
                    hard_block=False,
                    reason="ok",
                    estimated_cost_usd=min_cost,
                    max_estimated_cost_usd=cap,
                    value_tier=tier,
                ),
                costs,
                allowed,
            )

        return (
            BudgetDecision(
                allowed=False,
                hard_block=True,
                reason="over_cost_cap_or_unknown_pricing",
                estimated_cost_usd=min_cost,
                max_estimated_cost_usd=cap,
                value_tier=tier,
            ),
            costs,
            allowed,
        )

    def _bucket_key_for_selected(self, *, selected: SelectedEndpoint) -> tuple[str, str, str]:
        if selected.pool == "gemini_credit":
            project = self._gemini_project or "unknown_project"
            return ("project", project, selected.model)
        if selected.pool == "glm_free":
            aid = selected.account_id or "unknown_account"
            return ("account", aid, selected.model)
        return ("unknown", "unknown", selected.model)

    async def _get_or_create_quota_bucket(self, *, selected: SelectedEndpoint) -> QuotaBucket:
        scope, owner, model = self._bucket_key_for_selected(selected=selected)
        bucket_id = f"{scope}:{owner}:{model}"
        async with self._lock:
            b = self._quota_buckets.get(bucket_id)
            if b is not None:
                return b
            if scope == "project":
                max_inflight = int(self._gemini_bucket_max_inflight)
            else:
                max_inflight = 64
            b = QuotaBucket(
                bucket_id=bucket_id,
                scope=scope,
                model=model,
                max_inflight=_clamp_int(int(max_inflight), 1, 256),
                inflight=0,
                cooldown_until_s=0.0,
                recent_429=0,
                window_1h_start_s=_now_s(),
                window_1d_start_s=_now_s(),
            )
            self._quota_buckets[bucket_id] = b
            return b

    def _quota_can_admit(self, *, bucket: QuotaBucket) -> tuple[bool, str]:
        now = _now_s()
        if bucket.cooldown_until_s > now:
            return False, "cooldown"
        if bucket.inflight >= bucket.max_inflight:
            return False, "inflight_full"
        if bucket.scope == "project":
            if self._gemini_hourly_cap_usd is not None and bucket.window_1h_cost_usd >= float(self._gemini_hourly_cap_usd):
                return False, "hourly_cap"
            if self._gemini_daily_cap_usd is not None and bucket.window_1d_cost_usd >= float(self._gemini_daily_cap_usd):
                return False, "daily_cap"
        return True, "ok"

    async def _quota_on_admit(self, *, bucket: QuotaBucket) -> None:
        async with self._lock:
            bucket.inflight += 1

    async def _quota_on_release(self, *, bucket: QuotaBucket) -> None:
        async with self._lock:
            bucket.inflight = max(0, int(bucket.inflight) - 1)

    async def _quota_on_429(self, *, bucket: QuotaBucket, cooldown_s: float) -> None:
        async with self._lock:
            bucket.recent_429 = int(bucket.recent_429) + 1
            bucket.cooldown_until_s = max(float(bucket.cooldown_until_s), _now_s() + float(cooldown_s))

    async def _quota_on_result(
        self,
        *,
        bucket: QuotaBucket,
        usage: Optional[Mapping[str, Any]],
        model: str,
    ) -> None:
        if not isinstance(usage, Mapping):
            return
        pt = usage.get("prompt_tokens")
        ct = usage.get("completion_tokens")
        tt = usage.get("total_tokens")
        prompt_tokens = int(pt) if isinstance(pt, int) else 0
        completion_tokens = int(ct) if isinstance(ct, int) else 0
        total_tokens = int(tt) if isinstance(tt, int) else int(prompt_tokens + completion_tokens)
        cost = estimate_cost_usd(model=model, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
        now = _now_s()
        async with self._lock:
            if now - float(bucket.window_1h_start_s) >= 3600.0:
                bucket.window_1h_start_s = now
                bucket.window_1h_cost_usd = 0.0
                bucket.window_1h_tokens = 0
            if now - float(bucket.window_1d_start_s) >= 86400.0:
                bucket.window_1d_start_s = now
                bucket.window_1d_cost_usd = 0.0
                bucket.window_1d_tokens = 0
            bucket.window_1h_tokens += max(0, int(total_tokens))
            bucket.window_1d_tokens += max(0, int(total_tokens))
            if cost is not None:
                bucket.window_1h_cost_usd += float(cost)
                bucket.window_1d_cost_usd += float(cost)

    def _score_endpoint(
        self,
        *,
        selected: SelectedEndpoint,
        mp: ModelProfile,
        state: Optional[EndpointState],
        bucket: Optional[QuotaBucket],
        hints: RouteHints,
        profile: TaskProfile,
        difficulty: int,
    ) -> RouteScoreBreakdown:
        reasons: list[str] = []
        capability = 0.0
        congestion = 0.0
        cost_penalty = 0.0
        risk_penalty = 0.0
        policy_bonus = 0.0

        if mp.reserve_for_high_difficulty and difficulty < mp.min_difficulty:
            capability -= 50.0
            reasons.append("reserve_for_high_difficulty")

        if profile.has_tools and not mp.tool_call:
            capability -= 100.0
            reasons.append("no_tool_call")

        if profile.requires_thinking and mp.thinking == "none":
            capability -= 60.0
            reasons.append("no_thinking")

        if profile.prompt_tokens_est > mp.max_context:
            capability -= 200.0
            reasons.append("context_overflow")

        if state is not None:
            cong = float(state.inflight) / float(max(1, state.configured_concurrency))
            congestion -= 30.0 * cong
            if state.cooldown_until_s > _now_s():
                congestion -= 200.0
                reasons.append("endpoint_cooldown")
            if state.recent_1302_count > 0:
                risk_penalty -= 10.0 * float(state.recent_1302_count)
                reasons.append("recent_1302")
            if state.recent_1305_count > 0:
                risk_penalty -= 10.0 * float(state.recent_1305_count)
                reasons.append("recent_1305")

        if bucket is not None:
            ok, qreason = self._quota_can_admit(bucket=bucket)
            if not ok:
                congestion -= 150.0
                reasons.append(f"quota_{qreason}")
            if bucket.recent_429 > 0:
                risk_penalty -= 12.0 * float(bucket.recent_429)
                reasons.append("recent_429")

        if selected.pool == "gemini_credit":
            est = self._estimate_credit_cost_usd(model=selected.model, profile=profile)
            if est is None:
                cost_penalty -= 25.0
                reasons.append("unknown_cost")
            else:
                cost_penalty -= min(200.0, 500.0 * float(est))
                reasons.append("cost_estimated")

        budget_policy = self._normalize_budget_policy(hints.budget_policy)
        if budget_policy == "premium_only" and selected.pool == "gemini_credit":
            policy_bonus += 20.0
        if budget_policy in {"free_only", "prefer_free_then_credit"} and selected.pool == "glm_free":
            policy_bonus += 10.0
        if budget_policy == "free_only" and selected.pool == "gemini_credit":
            policy_bonus -= 200.0

        total = capability + congestion + cost_penalty + risk_penalty + policy_bonus
        return RouteScoreBreakdown(
            total=float(total),
            capability=float(capability),
            congestion=float(congestion),
            cost_penalty=float(cost_penalty),
            risk_penalty=float(risk_penalty),
            policy_bonus=float(policy_bonus),
            reasons=reasons,
        )

    async def route(
        self,
        *,
        request_body: Mapping[str, Any],
        headers: Mapping[str, str],
    ) -> dict[str, Any]:
        if not self._auth_ok(headers):
            return self._openai_error("unauthorized", status_code=401)

        if not self._router_auth_key:
            self._maybe_import_client_glm_key(headers=headers)

        hints = parse_route_hints(headers, request_body)
        profile = build_task_profile(request_body, hints)
        trace_id = hints.trace_id or str(uuid.uuid4())

        difficulty_initial = hints.difficulty if hints.difficulty is not None else estimate_initial_difficulty(profile)
        lane_initial = choose_lane(hints, profile, difficulty_initial)

        difficulty_final = difficulty_initial
        lane_final = lane_initial
        reclassified_by = None
        reclass_reason = None

        internal_classification = bool(request_body.get("_internal_classification"))
        if not internal_classification and difficulty_initial < 45:
            try:
                cr = await self._classifier.classify(request_body, profile)
            except ClassificationError as e:
                tl = _test_mode_level()
                raw = getattr(e, "raw", None)
                if tl < 2 and isinstance(raw, str):
                    raw = _truncate_str(raw, 256)
                debug = {
                    "selected_pool": None,
                    "selected_model": None,
                    "selected_account": None,
                    "difficulty_initial": difficulty_initial,
                    "difficulty_final": difficulty_initial,
                    "lane_initial": lane_initial,
                    "lane_final": lane_initial,
                    "reclassified_by": "classifier_error",
                    "reclass_reason": str(e),
                    "classifier_error": {
                        "kind": getattr(e, "kind", ""),
                        "account_id": getattr(e, "account_id", None),
                        "raw": raw,
                    },
                    "budget_policy": hints.budget_policy,
                    "trace_id": trace_id,
                }
                if tl >= 2:
                    debug["request"] = {"headers": dict(headers), "body": dict(request_body)}
                elif tl == 1:
                    debug["request_summary"] = _request_summary(headers=headers, request_body=request_body)
                return self._openai_error("classification_error", status_code=500, extra={"freepool": debug})
            difficulty_final = _normalize_difficulty(int(cr.difficulty))
            lane_final = str(cr.lane)
            reclassified_by = "glm4flash"
            reclass_reason = str(cr.reason or "")

        budget_decision, credit_costs, credit_allowed_models = await self._budget_decision_for_credit(
            hints=hints,
            profile=profile,
            lane=lane_final,
            difficulty=difficulty_final,
        )
        requested_model = request_body.get("model")
        if isinstance(requested_model, str) and requested_model.startswith("gemini-") and requested_model != "auto":
            cap = self._budget_cap_usd(hints=hints)
            est = self._estimate_credit_cost_usd(model=requested_model, profile=profile)
            credit_costs = dict(credit_costs)
            credit_costs[requested_model] = est
            if self._normalize_budget_policy(hints.budget_policy) == "free_only":
                budget_decision = BudgetDecision(
                    allowed=False,
                    hard_block=True,
                    reason="free_only",
                    estimated_cost_usd=est,
                    max_estimated_cost_usd=cap,
                    value_tier=(hints.value_tier or "").strip().lower() or None,
                )
                credit_allowed_models = set()
            elif cap is not None and (est is None or est > cap + 1e-9):
                budget_decision = BudgetDecision(
                    allowed=False,
                    hard_block=True,
                    reason="over_cost_cap_or_unknown_pricing",
                    estimated_cost_usd=est,
                    max_estimated_cost_usd=cap,
                    value_tier=(hints.value_tier or "").strip().lower() or None,
                )
                credit_allowed_models = set([requested_model]) if est is not None and est <= cap + 1e-9 else set()
            else:
                credit_allowed_models = set([requested_model])

        headers_with_trace = dict(headers)
        if _get_header(headers_with_trace, "x-freepool-trace-id") is None:
            headers_with_trace["x-freepool-trace-id"] = trace_id

        plan = await self._build_pool_plan(
            request_body=request_body,
            hints=hints,
            profile=profile,
            lane=lane_final,
            difficulty=difficulty_final,
        )
        planned_pool = plan[0][0] if plan else None

        selected: Optional[SelectedEndpoint] = None
        selected_pool = None
        selected_model = None
        selected_account = None

        attempt_chain: list[SelectedEndpoint] = []
        candidates_seen: list[dict[str, Any]] = []
        pool_unavailable_reason = None

        if plan:
            pool_name, model_profiles, pool_hard_empty = plan[0]
            if pool_name == "gemini_credit":
                if budget_decision.hard_block:
                    pool_hard_empty = True
                    model_profiles = []
                elif credit_allowed_models:
                    model_profiles = [m for m in model_profiles if m.model in credit_allowed_models]

            chain, candidates = await self._build_attempt_chain(
                pool=pool_name, models=model_profiles, hints=hints, profile=profile, difficulty=difficulty_final
            )
            candidates_seen.extend(candidates)
            if chain:
                selected = chain[0]
                selected_pool = selected.pool
                selected_model = selected.model
                selected_account = selected.account_id
                attempt_chain = [chain[0]]
            else:
                if not pool_hard_empty:
                    pool_unavailable_reason = f"{pool_name}_unavailable"

        quota_snapshot = None
        if selected is not None:
            b = await self._get_or_create_quota_bucket(selected=selected)
            ok, qreason = self._quota_can_admit(bucket=b)
            quota_snapshot = {
                "bucket_id": b.bucket_id,
                "admitted": bool(ok),
                "reason": qreason,
                "inflight": int(b.inflight),
                "max_inflight": int(b.max_inflight),
                "cooldown_until_s": float(b.cooldown_until_s),
                "recent_429": int(b.recent_429),
                "window_1h_cost_usd": float(b.window_1h_cost_usd),
                "window_1d_cost_usd": float(b.window_1d_cost_usd),
            }

        debug = {
            "selected_pool": selected_pool,
            "selected_model": selected_model,
            "selected_account": selected_account,
            "difficulty_initial": difficulty_initial,
            "difficulty_final": difficulty_final,
            "lane_initial": lane_initial,
            "lane_final": lane_final,
            "reclassified_by": reclassified_by,
            "reclass_reason": reclass_reason,
            "budget_policy": hints.budget_policy,
            "budget": {
                "allowed": bool(budget_decision.allowed),
                "hard_block": bool(budget_decision.hard_block),
                "reason": str(budget_decision.reason),
                "estimated_cost_usd": budget_decision.estimated_cost_usd,
                "max_estimated_cost_usd": budget_decision.max_estimated_cost_usd,
                "value_tier": budget_decision.value_tier,
                "credit_model_costs": credit_costs,
                "credit_allowed_models": sorted(list(credit_allowed_models)),
            },
            "quota": quota_snapshot,
            "candidates": candidates_seen,
            "attempt_chain": [
                {"pool": e.pool, "model": e.model, "account_id": e.account_id} for e in attempt_chain[:8]
            ],
            "trace_id": trace_id,
            "unavailable_reason": pool_unavailable_reason,
        }
        tl = _test_mode_level()
        if tl >= 2:
            debug["request"] = {"headers": dict(headers), "body": dict(request_body)}
            debug["headers_with_trace"] = dict(headers_with_trace)
        elif tl == 1:
            debug["request_summary"] = _request_summary(headers=headers, request_body=request_body)

        if self._dry_run:
            strip_candidates = tl < 2
            content = (
                "[DRY_RUN] "
                + json.dumps(
                    {k: v for k, v in debug.items() if (not strip_candidates) or k not in {"candidates"}},
                    ensure_ascii=False,
                )
            )
            return self._openai_chat_response(
                model=str(request_body.get("model") or "auto"),
                content=content,
                finish_reason="stop",
                extra={"freepool": debug},
            )

        if selected is None:
            if planned_pool == "gemini_credit" and budget_decision.hard_block:
                return self._openai_error("budget_blocked", status_code=403, extra={"freepool": debug})
            return self._openai_error("no_available_endpoint", status_code=503, extra={"freepool": debug})

        stream = bool(request_body.get("stream"))
        if stream:
            return {"__stream__": True, "selected": selected, "profile": profile, "debug": debug, "headers": headers_with_trace}

        try:
            result = await self._call_provider_chat(selected=selected, request_body=request_body, headers=headers_with_trace)
        except ProviderError as e:
            await self._apply_cooldown(selected=selected, err=e)
            debug["provider_error"] = {
                "pool": selected.pool,
                "model": selected.model,
                "account_id": selected.account_id,
                "code": e.code,
                "status_code": e.status_code,
                "message": str(e),
            }
            return self._openai_error(
                "upstream_error",
                status_code=int(e.status_code or 502),
                extra={"freepool": debug},
            )

        return self._openai_chat_response(
            model=str(request_body.get("model") or "auto"),
            content=str(result.get("content") or ""),
            finish_reason=str(result.get("finish_reason") or "stop"),
            usage=result.get("usage") if isinstance(result.get("usage"), dict) else None,
            extra={"freepool": debug},
        )

    async def stream_chat(
        self,
        *,
        selected: SelectedEndpoint,
        request_body: Mapping[str, Any],
        headers: Mapping[str, str],
        profile: TaskProfile,
        debug: Mapping[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        async for chunk in self._execute_stream(
            selected=selected,
            request_body=request_body,
            headers=headers,
            profile=profile,
            debug=dict(debug),
        ):
            yield chunk

    async def _build_pool_plan(
        self,
        *,
        request_body: Mapping[str, Any],
        hints: RouteHints,
        profile: TaskProfile,
        lane: str,
        difficulty: int,
    ) -> list[tuple[str, list[ModelProfile], bool]]:
        requested_model = request_body.get("model")
        if isinstance(requested_model, str) and requested_model and requested_model != "auto":
            mp = self._models.get(requested_model)
            if mp:
                return [(mp.pool, [mp], False)]
            if requested_model.startswith("gemini-"):
                return [("gemini_credit", [ModelProfile(
                    model=requested_model,
                    pool="gemini_credit",
                    modalities={"text", "image"},
                    max_context=1048576,
                    max_output=8192,
                    thinking="toggle",
                    tool_call=False,
                    base_concurrency=1,
                )], False)]
            return [("glm_free", [], True)]

        budget = hints.budget_policy.strip().lower()
        if budget not in {"free_only", "prefer_free_then_credit", "credit_allowed", "premium_only"}:
            budget = "free_only"

        free_models_raw = _glm_models_for_lane(lane, difficulty, profile)
        credit_models_raw = _gemini_models_for_lane(lane, profile)

        free_models = [self._models[m] for m in free_models_raw if m in self._models]
        credit_models = [self._models[m] for m in credit_models_raw if m in self._models]

        free_models = [m for m in free_models if await self._model_hard_ok(m, profile=profile, difficulty=difficulty)]
        credit_models = [m for m in credit_models if await self._model_hard_ok(m, profile=profile, difficulty=difficulty)]

        if budget == "premium_only":
            return [("gemini_credit", credit_models, not bool(credit_models))]
        if budget == "free_only":
            return [("glm_free", free_models, not bool(free_models))]
        if budget == "prefer_free_then_credit":
            if free_models:
                return [("glm_free", free_models, False)]
            return [("gemini_credit", credit_models, not bool(credit_models))]

        credit_first = difficulty >= 70 or lane in {"long_context"}
        if credit_first:
            return [
                ("gemini_credit", credit_models, not bool(credit_models)),
                ("glm_free", free_models, not bool(free_models)),
            ]
        return [
            ("glm_free", free_models, not bool(free_models)),
            ("gemini_credit", credit_models, not bool(credit_models)),
        ]

    async def _model_hard_ok(self, mp: ModelProfile, *, profile: TaskProfile, difficulty: int) -> bool:
        if profile.modality == "vision" and "image" not in mp.modalities:
            return False
        if profile.modality == "text" and "text" not in mp.modalities:
            return False
        if profile.has_tools and not mp.tool_call:
            return False
        if profile.requires_thinking and mp.thinking == "none":
            return False
        if profile.prompt_tokens_est > mp.max_context:
            return False
        if profile.requested_output_tokens and profile.requested_output_tokens > mp.max_output:
            return False
        if mp.reserve_for_high_difficulty and difficulty < mp.min_difficulty:
            return False
        if self._model_in_cooldown(mp.model):
            return False
        return True

    def _maybe_import_client_glm_key(self, *, headers: Mapping[str, str]) -> None:
        if self._glm_accounts:
            return
        auth = _get_header(headers, "authorization") or ""
        if not auth:
            return
        parts = auth.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1].strip()
        else:
            token = auth.strip()
        if not token:
            return
        self._glm_accounts["client"] = token

    async def _build_attempt_chain(
        self,
        *,
        pool: str,
        models: list[ModelProfile],
        hints: RouteHints,
        profile: TaskProfile,
        difficulty: int,
    ) -> tuple[list[SelectedEndpoint], list[dict[str, Any]]]:
        chain: list[SelectedEndpoint] = []
        candidates: list[dict[str, Any]] = []
        if pool == "glm_free":
            for mp in models:
                eps = await self._expand_glm_endpoints(model=mp.model)
                scored: list[tuple[float, SelectedEndpoint]] = []
                for ep in eps:
                    state = self._get_or_create_state(pool=ep.pool, account_id=ep.account_id or "__default__", model=ep.model)
                    bucket = await self._get_or_create_quota_bucket(selected=ep)
                    ok, qreason = self._quota_can_admit(bucket=bucket)
                    sb = self._score_endpoint(
                        selected=ep,
                        mp=mp,
                        state=state,
                        bucket=bucket,
                        hints=hints,
                        profile=profile,
                        difficulty=difficulty,
                    )
                    candidates.append(
                        {
                            "pool": ep.pool,
                            "model": ep.model,
                            "account_id": ep.account_id,
                            "quota": {"ok": bool(ok), "reason": qreason, "bucket_id": bucket.bucket_id},
                            "score": {
                                "total": sb.total,
                                "capability": sb.capability,
                                "congestion": sb.congestion,
                                "cost_penalty": sb.cost_penalty,
                                "risk_penalty": sb.risk_penalty,
                                "policy_bonus": sb.policy_bonus,
                                "reasons": sb.reasons,
                            },
                        }
                    )
                    if ok:
                        scored.append((sb.total, ep))
                scored.sort(key=lambda x: x[0], reverse=True)
                chain.extend([ep for _, ep in scored[:3]])
            return chain, candidates

        if pool == "gemini_credit":
            for mp in models:
                ep = SelectedEndpoint(pool="gemini_credit", model=mp.model, account_id=None)
                bucket = await self._get_or_create_quota_bucket(selected=ep)
                ok, qreason = self._quota_can_admit(bucket=bucket)
                sb = self._score_endpoint(
                    selected=ep,
                    mp=mp,
                    state=None,
                    bucket=bucket,
                    hints=hints,
                    profile=profile,
                    difficulty=difficulty,
                )
                candidates.append(
                    {
                        "pool": ep.pool,
                        "model": ep.model,
                        "account_id": ep.account_id,
                        "quota": {"ok": bool(ok), "reason": qreason, "bucket_id": bucket.bucket_id},
                        "score": {
                            "total": sb.total,
                            "capability": sb.capability,
                            "congestion": sb.congestion,
                            "cost_penalty": sb.cost_penalty,
                            "risk_penalty": sb.risk_penalty,
                            "policy_bonus": sb.policy_bonus,
                            "reasons": sb.reasons,
                        },
                    }
                )
                if ok:
                    chain.append(ep)
            return chain, candidates

        return [], []

    async def _expand_glm_endpoints(self, *, model: str) -> list[SelectedEndpoint]:
        async with self._lock:
            accounts = list(self._glm_accounts.keys())
            if not accounts:
                auth = os.environ.get("GLM_API_KEY") or None
                if auth:
                    self._glm_accounts["key_env"] = auth
                    accounts = ["key_env"]
            scored: list[tuple[int, str]] = []
            for aid in accounts:
                if self._account_in_cooldown(aid):
                    continue
                state = self._get_or_create_state(pool="glm_free", account_id=aid, model=model)
                if state.cooldown_until_s > _now_s():
                    continue
                scored.append((state.inflight, aid))
            scored.sort()
            out = []
            for _, aid in scored:
                state = self._get_or_create_state(pool="glm_free", account_id=aid, model=model)
                if state.inflight >= state.configured_concurrency:
                    continue
                out.append(SelectedEndpoint(pool="glm_free", model=model, account_id=aid))
            return out

    async def _call_provider_chat(
        self,
        *,
        selected: SelectedEndpoint,
        request_body: Mapping[str, Any],
        headers: Mapping[str, str],
    ) -> dict[str, Any]:
        account_id = selected.account_id or "__default__"
        async with self._lock:
            state = self._get_or_create_state(pool=selected.pool, account_id=account_id, model=selected.model)
        semaphore = state.semaphore
        messages = request_body.get("messages") if isinstance(request_body.get("messages"), list) else []
        bucket = await self._get_or_create_quota_bucket(selected=selected)
        ok, qreason = self._quota_can_admit(bucket=bucket)
        if not ok:
            raise ProviderError(f"quota:{qreason}", status_code=429)

        async with semaphore:
            await self._quota_on_admit(bucket=bucket)
            async with self._lock:
                state.inflight += 1
                state.last_used_at_s = _now_s()
            try:
                result = await self._provider.chat(
                    model=selected.model,
                    messages=messages,
                    headers=headers,
                    request_body=request_body,
                    selected=selected,
                )
                async with self._lock:
                    state.success_5m += 1
                await self._quota_on_result(
                    bucket=bucket,
                    usage=result.get("usage") if isinstance(result, dict) else None,
                    model=selected.model,
                )
                return result
            except ProviderError as e:
                async with self._lock:
                    state.fail_5m += 1
                if int(e.status_code or 0) == 429:
                    await self._quota_on_429(bucket=bucket, cooldown_s=20.0 if selected.pool == "glm_free" else 30.0)
                raise
            finally:
                async with self._lock:
                    state.inflight = max(0, state.inflight - 1)
                await self._quota_on_release(bucket=bucket)

    async def _select_endpoint(
        self,
        *,
        request_body: Mapping[str, Any],
        headers: Mapping[str, str],
        hints: RouteHints,
        profile: TaskProfile,
        lane: str,
        difficulty: int,
    ) -> tuple[Optional[SelectedEndpoint], list[dict[str, Any]]]:
        requested_model = request_body.get("model")
        if isinstance(requested_model, str) and requested_model and requested_model != "auto":
            mp = self._models.get(requested_model)
            if mp:
                return await self._select_specific_model(mp, profile=profile, difficulty=difficulty), [
                    {"pool": mp.pool, "model": mp.model}
                ]
            if requested_model.startswith("gemini-"):
                mp2 = self._models.get(requested_model)
                if mp2:
                    return await self._select_specific_model(mp2, profile=profile, difficulty=difficulty), [
                        {"pool": mp2.pool, "model": mp2.model}
                    ]

        budget = hints.budget_policy.strip().lower()
        if budget not in {"free_only", "prefer_free_then_credit", "credit_allowed", "premium_only"}:
            budget = "free_only"

        candidates: list[ModelProfile] = []
        glm_models = _glm_models_for_lane(lane, difficulty, profile)
        gemini_models = _gemini_models_for_lane(lane, profile)

        if budget == "premium_only":
            for m in gemini_models:
                if m in self._models:
                    candidates.append(self._models[m])
        elif budget == "free_only":
            for m in glm_models:
                if m in self._models:
                    candidates.append(self._models[m])
        elif budget == "prefer_free_then_credit":
            for m in glm_models:
                if m in self._models:
                    candidates.append(self._models[m])
            for m in gemini_models:
                if m in self._models:
                    candidates.append(self._models[m])
        else:
            free_first = difficulty < 65 and lane not in {"long_context"}
            if free_first:
                for m in glm_models:
                    if m in self._models:
                        candidates.append(self._models[m])
                for m in gemini_models:
                    if m in self._models:
                        candidates.append(self._models[m])
            else:
                for m in gemini_models:
                    if m in self._models:
                        candidates.append(self._models[m])
                for m in glm_models:
                    if m in self._models:
                        candidates.append(self._models[m])

        serialized_candidates: list[dict[str, Any]] = [{"pool": c.pool, "model": c.model} for c in candidates]
        for c in candidates:
            sel = await self._select_specific_model(c, profile=profile, difficulty=difficulty)
            if sel is not None:
                return sel, serialized_candidates
        return None, serialized_candidates

    async def _select_specific_model(
        self, mp: ModelProfile, *, profile: TaskProfile, difficulty: int
    ) -> Optional[SelectedEndpoint]:
        if profile.modality == "vision" and "image" not in mp.modalities:
            return None
        if profile.modality == "text" and "text" not in mp.modalities:
            return None
        if profile.prompt_tokens_est > mp.max_context:
            return None
        if profile.requested_output_tokens and profile.requested_output_tokens > mp.max_output:
            return None
        if mp.reserve_for_high_difficulty and difficulty < mp.min_difficulty:
            return None
        if mp.pool == "gemini_credit":
            if self._model_in_cooldown(mp.model):
                return None
            return SelectedEndpoint(pool="gemini_credit", model=mp.model, account_id=None)
        if mp.pool == "glm_free":
            if self._model_in_cooldown(mp.model):
                return None
            account_id = await self._pick_glm_account(model=mp.model, profile=profile)
            if not account_id:
                return None
            return SelectedEndpoint(pool="glm_free", model=mp.model, account_id=account_id)
        return None

    def _model_in_cooldown(self, model: str) -> bool:
        until = float(self._model_cooldown_until_s.get(model, 0.0))
        return until > _now_s()

    def _account_in_cooldown(self, account_id: str) -> bool:
        until = float(self._account_cooldown_until_s.get(account_id, 0.0))
        return until > _now_s()

    async def _pick_glm_account(self, *, model: str, profile: TaskProfile) -> Optional[str]:
        async with self._lock:
            accounts = list(self._glm_accounts.keys())
            if not accounts:
                auth = os.environ.get("GLM_API_KEY") or None
                if auth:
                    self._glm_accounts["key_env"] = auth
                    accounts = ["key_env"]
            best: Optional[tuple[int, str]] = None
            for aid in accounts:
                if self._account_in_cooldown(aid):
                    continue
                state = self._get_or_create_state(pool="glm_free", account_id=aid, model=model)
                if state.cooldown_until_s > _now_s():
                    continue
                if state.inflight >= state.configured_concurrency:
                    continue
                score = state.inflight
                cand = (score, aid)
                if best is None or cand < best:
                    best = cand
            return best[1] if best else None

    def _effective_concurrency(self, base: int, profile: TaskProfile) -> int:
        if profile.prompt_tokens_est > 64000:
            return max(1, base // 2)
        if profile.prompt_tokens_est > 32000:
            return max(1, (base * 2) // 3)
        return max(1, base)

    def _get_or_create_state(self, *, pool: str, model: str, account_id: str) -> EndpointState:
        key = (pool, model, account_id)
        state = self._endpoint_states.get(key)
        if state is not None:
            return state
        base = self._models[model].base_concurrency
        state = EndpointState(configured_concurrency=base, semaphore=asyncio.Semaphore(base))
        self._endpoint_states[key] = state
        return state

    async def _execute_chat(
        self,
        *,
        selected: SelectedEndpoint,
        request_body: Mapping[str, Any],
        headers: Mapping[str, str],
        profile: TaskProfile,
        debug: MutableMapping[str, Any],
    ) -> dict[str, Any]:
        account_id = selected.account_id or "__default__"
        async with self._lock:
            state = self._get_or_create_state(pool=selected.pool, account_id=account_id, model=selected.model)
        semaphore = state.semaphore

        messages = request_body.get("messages") if isinstance(request_body.get("messages"), list) else []

        async with semaphore:
            async with self._lock:
                state.inflight += 1
                state.last_used_at_s = _now_s()
            try:
                result = await self._provider.chat(
                    model=selected.model,
                    messages=messages,
                    headers=headers,
                    request_body=request_body,
                    selected=selected,
                )
                async with self._lock:
                    state.success_5m += 1
                return self._openai_chat_response(
                    model=str(request_body.get("model") or "auto"),
                    content=str(result.get("content") or ""),
                    finish_reason=str(result.get("finish_reason") or "stop"),
                    usage=result.get("usage") if isinstance(result.get("usage"), dict) else None,
                    extra={"freepool": debug},
                )
            except ProviderError as e:
                await self._apply_cooldown(selected=selected, err=e)
                async with self._lock:
                    state.fail_5m += 1
                debug["provider_error"] = {"code": e.code, "status_code": e.status_code, "message": str(e)}
                return self._openai_error(
                    "upstream_error",
                    status_code=int(e.status_code or 502),
                    extra={"freepool": debug},
                )
            finally:
                async with self._lock:
                    state.inflight = max(0, state.inflight - 1)

    async def _execute_stream(
        self,
        *,
        selected: SelectedEndpoint,
        request_body: Mapping[str, Any],
        headers: Mapping[str, str],
        profile: TaskProfile,
        debug: MutableMapping[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        messages = request_body.get("messages") if isinstance(request_body.get("messages"), list) else []
        account_id = selected.account_id or "__default__"
        async with self._lock:
            state = self._get_or_create_state(pool=selected.pool, account_id=account_id, model=selected.model)
        semaphore = state.semaphore
        bucket = await self._get_or_create_quota_bucket(selected=selected)
        ok, qreason = self._quota_can_admit(bucket=bucket)
        if not ok:
            debug["provider_error"] = {"code": None, "status_code": 429, "message": f"quota:{qreason}"}
            yield {"__error__": self._openai_error("upstream_error", status_code=429)}
            return

        async with semaphore:
            await self._quota_on_admit(bucket=bucket)
            async with self._lock:
                state.inflight += 1
                state.last_used_at_s = _now_s()
            try:
                async for chunk in self._provider.stream(
                    model=selected.model,
                    messages=messages,
                    headers=headers,
                    request_body=request_body,
                    selected=selected,
                ):
                    yield chunk
                async with self._lock:
                    state.success_5m += 1
            except ProviderError as e:
                async with self._lock:
                    state.fail_5m += 1
                if int(e.status_code or 0) == 429:
                    await self._quota_on_429(bucket=bucket, cooldown_s=20.0 if selected.pool == "glm_free" else 30.0)
                await self._apply_cooldown(selected=selected, err=e)
                debug["provider_error"] = {"code": e.code, "status_code": e.status_code, "message": str(e)}
                yield {"__error__": self._openai_error("upstream_error", status_code=int(e.status_code or 502))}
            finally:
                async with self._lock:
                    state.inflight = max(0, state.inflight - 1)
                await self._quota_on_release(bucket=bucket)

    async def _apply_cooldown(self, *, selected: SelectedEndpoint, err: ProviderError) -> None:
        now = _now_s()
        code = err.code
        async with self._lock:
            if code in {1302} and selected.pool == "glm_free" and selected.account_id:
                key = ("glm_free", selected.model, selected.account_id)
                st = self._endpoint_states.get(key)
                if st:
                    st.cooldown_until_s = max(st.cooldown_until_s, now + 30.0)
                    st.recent_1302_count += 1
            if code in {1305}:
                self._model_cooldown_until_s[selected.model] = max(
                    float(self._model_cooldown_until_s.get(selected.model, 0.0)), now + 20.0
                )
            if code in {1304, 1308, 1310} and selected.pool == "glm_free" and selected.account_id:
                self._account_cooldown_until_s[selected.account_id] = max(
                    float(self._account_cooldown_until_s.get(selected.account_id, 0.0)), now + 3600.0
                )

    def _openai_chat_response(
        self,
        *,
        model: str,
        content: str,
        finish_reason: str,
        usage: Optional[Mapping[str, Any]] = None,
        extra: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        resp: dict[str, Any] = {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": int(_now_s()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": finish_reason,
                }
            ],
        }
        if usage:
            resp["usage"] = dict(usage)
        if extra:
            resp.update(extra)
        return resp

    def _openai_error(self, code: str, *, status_code: int, extra: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
        resp: dict[str, Any] = {
            "__http_status__": status_code,
            "error": {"message": code, "type": "router_error", "code": code},
        }
        if extra:
            resp.update(extra)
        return resp


def build_default_classifier(
    *, provider: Optional["ProviderAdapter"] = None, glm_account_keys: Optional[Mapping[str, str]] = None
) -> DifficultyClassifier:
    mode = (os.environ.get("FREEPOOL_CLASSIFIER_MODE") or "real").strip().lower()
    if mode != "real":
        raise RuntimeError("FREEPOOL_CLASSIFIER_MODE must be 'real'")
    if provider is None:
        raise RuntimeError("missing provider for real classifier")
    if glm_account_keys is None or len(glm_account_keys) == 0:
        raise RuntimeError("missing FREEPOOL_GLM_KEYS/GLM_API_KEY for real classifier")
    model = (os.environ.get("FREEPOOL_CLASSIFIER_MODEL") or "GLM-4-Flash").strip() or "GLM-4-Flash"
    return RealDifficultyClassifier(provider=provider, glm_account_keys=glm_account_keys, model=model)
