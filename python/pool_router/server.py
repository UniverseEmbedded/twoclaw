import json
import logging
import os
import time
import uuid
from typing import Any, AsyncIterator, Mapping, Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from .core import Router, SelectedEndpoint, TaskProfile

logger = logging.getLogger("uvicorn.error")


def _now_s() -> int:
    return int(time.time())


def _collect_headers(req: Request) -> dict[str, str]:
    return {k: v for k, v in req.headers.items()}


def _short_reason(v: Any) -> str:
    if not isinstance(v, str):
        return ""
    s = v.strip().replace("\n", " ")
    if len(s) <= 160:
        return s
    return s[:160]


def _test_mode_enabled() -> bool:
    return _test_mode_level() >= 1


def _test_mode_level() -> int:
    raw = (os.environ.get("FREEPOOL_TEST_MODE") or "").strip()
    if not raw:
        return 0
    try:
        return max(0, int(raw))
    except Exception:
        return 1


def _truncate_log(s: str, max_len: int) -> str:
    if len(s) <= max_len:
        return s
    return s[:max_len] + f"...(truncated,len={len(s)})"



def _log_chat_route(*, req: Request, body: Mapping[str, Any], freepool: Mapping[str, Any]) -> None:
    model = str(body.get("model") or "auto")
    d0 = freepool.get("difficulty_initial")
    d1 = freepool.get("difficulty_final")
    pool = freepool.get("selected_pool")
    sel_model = freepool.get("selected_model")
    who = freepool.get("reclassified_by")
    reason = _short_reason(freepool.get("reclass_reason"))
    basis = f"{who}:{reason}" if (who and reason) else ""
    score = f"{d0}->{d1}" if d0 is not None and d1 is not None else str(d1 if d1 is not None else d0)
    ua = req.headers.get("user-agent") or ""
    rid = freepool.get("trace_id") or ""
    logger.info(
        'route model=%s score=%s basis=%s -> %s/%s trace=%s ua=%s',
        model,
        score,
        basis,
        pool,
        sel_model,
        rid,
        ua,
    )
    tl = _test_mode_level()
    if tl >= 2:
        payload = {
            "trace_id": rid,
            "headers": _collect_headers(req),
            "body": dict(body),
            "freepool": dict(freepool),
        }
        logger.info("route_debug %s", _truncate_log(json.dumps(payload, ensure_ascii=False), 50000))
    elif tl == 1:
        fp = dict(freepool)
        keep = {
            "trace_id": fp.get("trace_id"),
            "selected_pool": fp.get("selected_pool"),
            "selected_model": fp.get("selected_model"),
            "selected_account": fp.get("selected_account"),
            "difficulty_initial": fp.get("difficulty_initial"),
            "difficulty_final": fp.get("difficulty_final"),
            "lane_initial": fp.get("lane_initial"),
            "lane_final": fp.get("lane_final"),
            "reclassified_by": fp.get("reclassified_by"),
            "reclass_reason": fp.get("reclass_reason"),
            "budget_policy": fp.get("budget_policy"),
            "budget": fp.get("budget"),
            "quota": fp.get("quota"),
            "provider_error": fp.get("provider_error"),
            "classifier_error": fp.get("classifier_error"),
            "unavailable_reason": fp.get("unavailable_reason"),
            "request_summary": fp.get("request_summary"),
        }
        logger.info("route_debug %s", _truncate_log(json.dumps({"trace_id": rid, "freepool": keep}, ensure_ascii=False), 12000))


def _openai_stream_event(
    *,
    stream_id: str,
    model: str,
    created: int,
    delta: Optional[Mapping[str, Any]] = None,
    finish_reason: Optional[str] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": stream_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": dict(delta or {}), "finish_reason": finish_reason}],
    }
    return payload


def create_app(*, router: Router) -> FastAPI:
    app = FastAPI(title="FreePool Router", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    @app.get("/v1/models")
    async def list_models():
        data = await router.list_models()
        return {"object": "list", "data": data}

    @app.post("/v1/chat/completions")
    async def chat_completions(req: Request):
        headers = _collect_headers(req)
        body = await req.json()
        if not isinstance(body, dict):
            return JSONResponse({"error": {"message": "invalid_request"}}, status_code=400)

        result = await router.route(request_body=body, headers=headers)
        if result.get("__stream__") is True:
            selected: SelectedEndpoint = result["selected"]
            profile: TaskProfile = result["profile"]
            debug: dict[str, Any] = result["debug"]
            stream_headers = result.get("headers") if isinstance(result.get("headers"), dict) else headers
            if isinstance(debug, dict):
                _log_chat_route(req=req, body=body, freepool=debug)
            stream_id = f"chatcmpl-{uuid.uuid4().hex}"
            created = _now_s()
            req_model = str(body.get("model") or "auto")

            async def gen() -> AsyncIterator[str]:
                yield f"data: {json.dumps(_openai_stream_event(stream_id=stream_id, model=req_model, created=created, delta={'role': 'assistant'}), ensure_ascii=False)}\n\n"
                async for chunk in router.stream_chat(
                    selected=selected, request_body=body, headers=stream_headers, profile=profile, debug=debug
                ):
                    if isinstance(chunk, dict) and "__error__" in chunk:
                        err = chunk["__error__"]
                        yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
                        break
                    delta = chunk.get("delta") if isinstance(chunk, dict) else None
                    if isinstance(delta, str) and delta:
                        yield f"data: {json.dumps(_openai_stream_event(stream_id=stream_id, model=req_model, created=created, delta={'content': delta}), ensure_ascii=False)}\n\n"
                    finish_reason = chunk.get("finish_reason") if isinstance(chunk, dict) else None
                    if finish_reason:
                        yield f"data: {json.dumps(_openai_stream_event(stream_id=stream_id, model=req_model, created=created, delta={}, finish_reason=str(finish_reason)), ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                gen(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
            )

        status = result.pop("__http_status__", None)
        if isinstance(status, int):
            fp = result.get("freepool")
            if isinstance(fp, dict):
                _log_chat_route(req=req, body=body, freepool=fp)
            tid = fp.get("trace_id") if isinstance(fp, dict) else None
            resp_headers = {"x-freepool-trace-id": str(tid)} if isinstance(tid, str) and tid else {}
            return JSONResponse(result, status_code=status, headers=resp_headers)
        fp = result.get("freepool")
        if isinstance(fp, dict):
            _log_chat_route(req=req, body=body, freepool=fp)
        tid = fp.get("trace_id") if isinstance(fp, dict) else None
        resp_headers = {"x-freepool-trace-id": str(tid)} if isinstance(tid, str) and tid else {}
        return JSONResponse(result, status_code=200, headers=resp_headers)

    return app
