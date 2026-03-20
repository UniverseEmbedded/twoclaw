from typing import Any, Mapping, Sequence

import httpx
from fastapi import APIRouter, HTTPException, Request, Response


router = APIRouter()


def _get_service(req: Request):
    svc = getattr(req.app.state, "mem1_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="mem1_not_ready")
    return svc


def _header_memory_mode(req: Request) -> str | None:
    raw = (req.headers.get("X-Memory-Mode") or "").strip().lower()
    if not raw:
        return None
    if raw not in {"off", "read", "readwrite"}:
        raise HTTPException(status_code=400, detail="invalid_memory_mode")
    return raw


def _extract_text_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes, bytearray)):
        parts: list[str] = []
        for part in content:
            if not isinstance(part, Mapping):
                continue
            if str(part.get("type") or "").strip() != "text":
                continue
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
        return "\n".join(parts).strip()
    return ""


def _last_user_query(messages: Sequence[Mapping[str, Any]]) -> str:
    for m in reversed(messages):
        if not isinstance(m, Mapping):
            continue
        if str(m.get("role") or "").strip() != "user":
            continue
        text = _extract_text_content(m.get("content"))
        if text:
            return text
    return ""


def _format_memories(hits: Sequence[Mapping[str, Any]], *, max_chars: int) -> str:
    lines: list[str] = ["[MEMORIES]"]
    used = len(lines[0])
    for hit in hits:
        memory = str(hit.get("memory") or "").strip()
        if not memory:
            continue
        score_val = hit.get("score")
        score = f"{float(score_val):.3f}" if isinstance(score_val, (float, int)) else "n/a"
        line = f"- (score={score}) {memory}"
        if used + 1 + len(line) > max_chars:
            break
        lines.append(line)
        used += 1 + len(line)
    return "\n".join(lines) if len(lines) > 1 else ""


def _inject_memory_block(messages: Sequence[Mapping[str, Any]], memory_block: str) -> list[dict[str, Any]]:
    if not memory_block:
        return [dict(m) for m in messages if isinstance(m, Mapping)]
    out = [dict(m) for m in messages if isinstance(m, Mapping)]
    insert_at = 0
    for i, m in enumerate(out):
        if str(m.get("role") or "").strip() == "system":
            insert_at = i + 1
            continue
        break
    out.insert(insert_at, {"role": "system", "content": memory_block})
    return out


def _resolve_memory_switches(
    *,
    mode: str | None,
    body_memory: Mapping[str, Any],
    default_enabled: bool,
) -> tuple[bool, bool]:
    if mode == "off":
        return False, False
    if mode == "read":
        return True, False
    if mode == "readwrite":
        return True, True
    enabled = body_memory.get("enabled")
    writeback = body_memory.get("writeback")
    resolved_enabled = bool(enabled) if isinstance(enabled, bool) else default_enabled
    resolved_writeback = bool(writeback) if isinstance(writeback, bool) else default_enabled
    if not resolved_enabled:
        resolved_writeback = False
    return resolved_enabled, resolved_writeback


def _safe_int(v: Any, default: int, *, low: int, high: int) -> int:
    try:
        n = int(v)
    except Exception:
        return default
    return max(low, min(high, n))


def _forward_headers(req: Request) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in req.headers.items():
        lk = k.lower()
        if lk in {"host", "content-length"}:
            continue
        if lk == "x-memory-mode":
            continue
        out[k] = v
    return out


@router.post("/v1/chat/completions")
async def chat_completions(req: Request):
    body = await req.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="invalid_request")
    messages = body.get("messages")
    if not isinstance(messages, list):
        raise HTTPException(status_code=400, detail="invalid_messages")

    svc = _get_service(req)
    mode = _header_memory_mode(req)

    user_id = body.pop("user_id", None)
    agent_id = body.pop("agent_id", None)
    run_id = body.pop("run_id", None)
    metadata = body.pop("metadata", None)
    memory_cfg_raw = body.pop("memory", None)
    body.pop("filters", None)
    limit = _safe_int(body.pop("limit", 10), 10, low=1, high=100)

    memory_cfg = memory_cfg_raw if isinstance(memory_cfg_raw, Mapping) else {}
    max_context_chars = _safe_int(memory_cfg.get("max_context_chars", 4000), 4000, low=256, high=20000)
    has_scope = any(x is not None and str(x).strip() for x in [user_id, agent_id, run_id])
    memory_enabled, memory_writeback = _resolve_memory_switches(
        mode=mode,
        body_memory=memory_cfg,
        default_enabled=has_scope,
    )

    search_hits: list[dict[str, Any]] = []
    outbound_messages = [dict(m) for m in messages if isinstance(m, Mapping)]
    query = _last_user_query(outbound_messages)
    if memory_enabled and has_scope and query:
        try:
            search_hits = svc.search(
                query=query,
                user_id=str(user_id) if user_id is not None else None,
                agent_id=str(agent_id) if agent_id is not None else None,
                run_id=str(run_id) if run_id is not None else None,
                limit=limit,
            )
        except Exception:
            search_hits = []
        block = _format_memories(search_hits, max_chars=max_context_chars)
        outbound_messages = _inject_memory_block(outbound_messages, block)

    body["messages"] = outbound_messages
    base_url = svc.settings.router_base_url.rstrip("/")
    target_url = f"{base_url}/v1/chat/completions"
    timeout = float(svc.settings.proxy_timeout_secs)
    headers = _forward_headers(req)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(target_url, headers=headers, json=body)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"router_unavailable: {e}")

    should_write = memory_writeback and has_scope and not bool(body.get("stream"))
    if should_write:
        try:
            payload = resp.json()
        except Exception:
            payload = None
        assistant = ""
        if isinstance(payload, dict):
            choices = payload.get("choices")
            if isinstance(choices, list) and choices:
                c0 = choices[0]
                if isinstance(c0, Mapping):
                    msg = c0.get("message")
                    if isinstance(msg, Mapping):
                        assistant = _extract_text_content(msg.get("content"))
        if assistant:
            try:
                write_messages = list(messages) + [{"role": "assistant", "content": assistant}]
                svc.add_memory(
                    messages=write_messages,
                    user_id=str(user_id) if user_id is not None else None,
                    agent_id=str(agent_id) if agent_id is not None else None,
                    run_id=str(run_id) if run_id is not None else None,
                    metadata=metadata if isinstance(metadata, Mapping) else None,
                )
            except Exception:
                pass

    response_headers: dict[str, str] = {}
    ct = resp.headers.get("content-type")
    if ct:
        response_headers["content-type"] = ct
    return Response(content=resp.content, status_code=resp.status_code, headers=response_headers)
