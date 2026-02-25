import json
import os
from typing import Any, AsyncIterator, Mapping, Optional

import httpx

from .core import MockProviderAdapter, ProviderAdapter, ProviderError, SelectedEndpoint


def _strip_internal_fields(body: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(body)
    out.pop("_freepool", None)
    out.pop("_internal_classification", None)
    return out


def _parse_upstream_error(payload: Any) -> tuple[Optional[int], str]:
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict):
            code = err.get("code")
            msg = err.get("message")
            return (int(code) if isinstance(code, int) else None, str(msg) if msg is not None else "error")
        if isinstance(payload.get("code"), int):
            return int(payload["code"]), str(payload.get("message") or "error")
    return None, "error"


class ZaiOpenAiAdapter:
    def __init__(
        self,
        *,
        base_url: str,
        account_keys: Mapping[str, str],
        timeout_s: float = 120.0,
    ):
        self._base_url = base_url.rstrip("/")
        self._account_keys = account_keys
        self._timeout = httpx.Timeout(timeout_s)
        self._client = httpx.AsyncClient(timeout=self._timeout)

    async def close(self) -> None:
        await self._client.aclose()

    def _auth_header(self, account_id: str) -> str:
        key = self._account_keys.get(account_id)
        if not key:
            raise ProviderError("missing_glm_key", status_code=500)
        return f"Bearer {key}"

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        headers: Mapping[str, str],
        request_body: Mapping[str, Any],
        selected: SelectedEndpoint,
    ) -> dict[str, Any]:
        if not selected.account_id:
            raise ProviderError("missing_account", status_code=500)

        url = f"{self._base_url}/chat/completions"
        body = _strip_internal_fields(request_body)
        body["model"] = model
        body["messages"] = messages
        body["stream"] = False

        resp = await self._client.post(
            url,
            headers={"Authorization": self._auth_header(selected.account_id), "Content-Type": "application/json"},
            json=body,
        )
        if resp.status_code >= 400:
            try:
                payload = resp.json()
            except Exception:
                payload = None
            code, msg = _parse_upstream_error(payload)
            raise ProviderError(msg, code=code, status_code=resp.status_code)

        data = resp.json()
        content = ""
        finish_reason = "stop"
        usage = data.get("usage") if isinstance(data, dict) else None
        try:
            choice0 = (data.get("choices") or [])[0]
            finish_reason = choice0.get("finish_reason") or finish_reason
            msg0 = choice0.get("message") or {}
            content = msg0.get("content") or ""
        except Exception:
            content = ""
        return {
            "content": content,
            "usage": usage if isinstance(usage, dict) else None,
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
        if not selected.account_id:
            raise ProviderError("missing_account", status_code=500)

        url = f"{self._base_url}/chat/completions"
        body = _strip_internal_fields(request_body)
        body["model"] = model
        body["messages"] = messages
        body["stream"] = True

        async with self._client.stream(
            "POST",
            url,
            headers={"Authorization": self._auth_header(selected.account_id), "Content-Type": "application/json"},
            json=body,
        ) as resp:
            if resp.status_code >= 400:
                raw = await resp.aread()
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except Exception:
                    payload = None
                code, msg = _parse_upstream_error(payload)
                raise ProviderError(msg, code=code, status_code=resp.status_code)

            async for line in resp.aiter_lines():
                if not line:
                    continue
                if not line.startswith("data:"):
                    continue
                data_part = line[len("data:") :].strip()
                if data_part == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_part)
                except Exception:
                    continue
                try:
                    choice0 = (chunk.get("choices") or [])[0]
                    delta = (choice0.get("delta") or {}).get("content")
                    finish_reason = choice0.get("finish_reason")
                    if isinstance(delta, str) and delta:
                        yield {"delta": delta}
                    if finish_reason:
                        yield {"finish_reason": finish_reason}
                except Exception:
                    continue

    async def list_models(self) -> list[dict[str, Any]]:
        url = f"{self._base_url}/models"
        resp = await self._client.get(url)
        if resp.status_code >= 400:
            return []
        data = resp.json()
        if isinstance(data, dict) and isinstance(data.get("data"), list):
            return [m for m in data["data"] if isinstance(m, dict)]
        if isinstance(data, dict) and isinstance(data.get("models"), list):
            return [m for m in data["models"] if isinstance(m, dict)]
        return []


class GeminiAdapter:
    def __init__(self):
        from google import genai
        from google.genai import types

        self._types = types
        vertexai = (os.environ.get("FREEPOOL_GEMINI_VERTEXAI") or "1").strip() == "1"
        api_key = os.environ.get("FREEPOOL_GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY") or ""
        project = os.environ.get("FREEPOOL_GEMINI_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT") or ""
        location = os.environ.get("FREEPOOL_GEMINI_LOCATION") or os.environ.get("GOOGLE_CLOUD_LOCATION") or ""

        if not project:
            cred = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or ""
            if cred and os.path.exists(cred):
                try:
                    with open(cred, "r", encoding="utf-8") as f:
                        raw = json.load(f)
                    pid = raw.get("project_id") if isinstance(raw, dict) else None
                    if isinstance(pid, str) and pid:
                        project = pid
                except Exception:
                    project = project

        if not api_key and not vertexai:
            self._client = None
            return

        kwargs: dict[str, Any] = {"vertexai": vertexai}
        if api_key:
            kwargs["api_key"] = api_key
        if project:
            kwargs["project"] = project
        if location:
            kwargs["location"] = location

        try:
            self._client = genai.Client(**kwargs)
        except TypeError:
            kwargs.pop("project", None)
            kwargs.pop("location", None)
            try:
                self._client = genai.Client(**kwargs)
            except Exception:
                self._client = None
        except Exception:
            self._client = None

    def _convert_messages(self, messages: list[dict[str, Any]]) -> list[Any]:
        contents = []
        system_content: Optional[str] = None

        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")
            if role == "system" and isinstance(content, str):
                system_content = content if system_content is None else system_content + "\n" + content
                continue

            if isinstance(content, str):
                contents.append(
                    self._types.Content(role=role, parts=[self._types.Part(text=content)])
                )
                continue

            if isinstance(content, list):
                parts = []
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    ptype = part.get("type")
                    if ptype == "text" and isinstance(part.get("text"), str):
                        parts.append(self._types.Part(text=part["text"]))
                if parts:
                    contents.append(self._types.Content(role=role, parts=parts))

        if system_content and contents:
            first = contents[0]
            if getattr(first, "role", None) == "user":
                prefix = f"[系统指令]\n{system_content}\n\n[用户消息]\n"
                first.parts[0].text = prefix + first.parts[0].text

        return contents

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        headers: Mapping[str, str],
        request_body: Mapping[str, Any],
        selected: SelectedEndpoint,
    ) -> dict[str, Any]:
        if self._client is None:
            raise ProviderError("gemini_not_configured", status_code=500)

        max_tokens = request_body.get("max_tokens") or request_body.get("max_completion_tokens") or 0
        temperature = request_body.get("temperature", 1.0)
        top_p = request_body.get("top_p", 0.95)

        cfg = self._types.GenerateContentConfig(
            temperature=float(temperature) if isinstance(temperature, (int, float)) else 1.0,
            top_p=float(top_p) if isinstance(top_p, (int, float)) else 0.95,
            max_output_tokens=int(max_tokens) if isinstance(max_tokens, int) else None,
        )

        contents = self._convert_messages(messages)
        response = self._client.models.generate_content(model=model, contents=contents, config=cfg)
        usage_md = getattr(response, "usage_metadata", None)
        usage = None
        if usage_md is not None:
            usage = {
                "prompt_tokens": getattr(usage_md, "prompt_token_count", None),
                "completion_tokens": getattr(usage_md, "candidates_token_count", None),
                "total_tokens": getattr(usage_md, "total_token_count", None),
            }
            usage = {k: v for k, v in usage.items() if isinstance(v, int)}
        return {"content": getattr(response, "text", "") or "", "usage": usage, "finish_reason": "stop"}

    async def stream(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        headers: Mapping[str, str],
        request_body: Mapping[str, Any],
        selected: SelectedEndpoint,
    ) -> AsyncIterator[dict[str, Any]]:
        if self._client is None:
            raise ProviderError("gemini_not_configured", status_code=500)

        max_tokens = request_body.get("max_tokens") or request_body.get("max_completion_tokens") or 0
        temperature = request_body.get("temperature", 1.0)
        top_p = request_body.get("top_p", 0.95)

        cfg = self._types.GenerateContentConfig(
            temperature=float(temperature) if isinstance(temperature, (int, float)) else 1.0,
            top_p=float(top_p) if isinstance(top_p, (int, float)) else 0.95,
            max_output_tokens=int(max_tokens) if isinstance(max_tokens, int) else None,
        )
        contents = self._convert_messages(messages)
        for chunk in self._client.models.generate_content_stream(model=model, contents=contents, config=cfg):
            text = getattr(chunk, "text", None) or ""
            if text:
                yield {"delta": text}
        yield {"finish_reason": "stop"}

    async def list_models(self) -> list[dict[str, Any]]:
        if self._client is None:
            return []
        out: list[dict[str, Any]] = []
        try:
            for m in self._client.models.list():
                mid = getattr(m, "name", None)
                if isinstance(mid, str) and mid.startswith("models/"):
                    out.append({"id": mid[len("models/") :], "object": "model"})
        except Exception:
            return []
        return out


class MultiProviderAdapter(ProviderAdapter):
    def __init__(
        self,
        *,
        glm: ZaiOpenAiAdapter,
        gemini: GeminiAdapter,
    ):
        self._glm = glm
        self._gemini = gemini

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        headers: Mapping[str, str],
        request_body: Mapping[str, Any],
        selected: SelectedEndpoint,
    ) -> dict[str, Any]:
        if selected.pool == "glm_free":
            return await self._glm.chat(
                model=model, messages=messages, headers=headers, request_body=request_body, selected=selected
            )
        if selected.pool == "gemini_credit":
            return await self._gemini.chat(
                model=model, messages=messages, headers=headers, request_body=request_body, selected=selected
            )
        raise ProviderError("unknown_pool", status_code=500)

    async def stream(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        headers: Mapping[str, str],
        request_body: Mapping[str, Any],
        selected: SelectedEndpoint,
    ) -> AsyncIterator[dict[str, Any]]:
        if selected.pool == "glm_free":
            async for it in self._glm.stream(
                model=model, messages=messages, headers=headers, request_body=request_body, selected=selected
            ):
                yield it
            return
        if selected.pool == "gemini_credit":
            async for it in self._gemini.stream(
                model=model, messages=messages, headers=headers, request_body=request_body, selected=selected
            ):
                yield it
            return
        raise ProviderError("unknown_pool", status_code=500)

    async def list_models(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        out.extend(await self._glm.list_models())
        out.extend(await self._gemini.list_models())
        return out


def build_default_provider(*, glm_account_keys: Mapping[str, str]) -> ProviderAdapter:
    mode = (os.environ.get("FREEPOOL_PROVIDER_MODE") or "real").strip().lower()
    if mode == "mock":
        cfg_raw = os.environ.get("FREEPOOL_MOCK_MODEL_CONFIG") or "{}"
        try:
            cfg = json.loads(cfg_raw)
        except Exception:
            cfg = {}
        if not isinstance(cfg, dict):
            cfg = {}
        return MockProviderAdapter(cfg)

    glm_base_url = os.environ.get("FREEPOOL_GLM_BASE_URL") or "https://open.bigmodel.cn/api/paas/v4"
    glm = ZaiOpenAiAdapter(base_url=glm_base_url, account_keys=glm_account_keys)
    gemini = GeminiAdapter()
    return MultiProviderAdapter(glm=glm, gemini=gemini)
