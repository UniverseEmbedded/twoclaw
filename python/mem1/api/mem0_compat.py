from typing import Any, Mapping

from fastapi import APIRouter, HTTPException, Request


def _get_service(req: Request):
    svc = getattr(req.app.state, "mem1_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="mem1_not_ready")
    return svc


router = APIRouter()


@router.get("/")
async def root():
    return {"status": "ok", "version": "1.0.0", "service": "mem1"}


@router.get("/healthz")
async def healthz():
    return {"ok": True}


@router.post("/configure")
async def configure(req: Request):
    body = await req.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="invalid_request")
    svc = _get_service(req)
    svc.configure(body)
    return {"message": "Configuration set successfully"}


@router.post("/memories")
async def add_memories(req: Request):
    body = await req.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="invalid_request")
    messages = body.get("messages")
    if not isinstance(messages, list):
        raise HTTPException(status_code=400, detail="invalid_messages")
    user_id = body.get("user_id")
    agent_id = body.get("agent_id")
    run_id = body.get("run_id")
    metadata = body.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        raise HTTPException(status_code=400, detail="invalid_metadata")
    svc = _get_service(req)
    try:
        item = svc.add_memory(
            messages=messages,
            user_id=str(user_id) if user_id is not None else None,
            agent_id=str(agent_id) if agent_id is not None else None,
            run_id=str(run_id) if run_id is not None else None,
            metadata=metadata,
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"embedder_failed: {e}")
    return {"results": [{"id": item["id"], "memory": item["memory"], "event": "ADD", "metadata": item["metadata"]}]}


@router.get("/memories")
async def list_memories(
    req: Request,
    user_id: str | None = None,
    agent_id: str | None = None,
    run_id: str | None = None,
    limit: int = 100,
):
    svc = _get_service(req)
    return svc.list_memories(user_id=user_id, agent_id=agent_id, run_id=run_id, limit=limit)


@router.get("/memories/{memory_id}")
async def get_memory(req: Request, memory_id: str):
    svc = _get_service(req)
    item = svc.get_memory(memory_id)
    if item is None:
        raise HTTPException(status_code=404, detail="not_found")
    return item


@router.put("/memories/{memory_id}")
async def update_memory(req: Request, memory_id: str):
    body = await req.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="invalid_request")
    memory = body.get("memory")
    metadata = body.get("metadata")
    if memory is not None and not isinstance(memory, str):
        raise HTTPException(status_code=400, detail="invalid_memory")
    if metadata is not None and not isinstance(metadata, dict):
        raise HTTPException(status_code=400, detail="invalid_metadata")
    svc = _get_service(req)
    item = svc.update_memory(memory_id=memory_id, memory=memory, metadata=metadata)
    if item is None:
        raise HTTPException(status_code=404, detail="not_found")
    return {"id": item["id"], "memory": item["memory"], "event": "UPDATE", "metadata": item["metadata"]}


@router.delete("/memories/{memory_id}")
async def delete_memory(req: Request, memory_id: str):
    svc = _get_service(req)
    ok = svc.delete_memory(memory_id=memory_id)
    if not ok:
        raise HTTPException(status_code=404, detail="not_found")
    return {"message": "Memory deleted successfully"}


@router.delete("/memories")
async def delete_memories(req: Request, user_id: str | None = None, agent_id: str | None = None, run_id: str | None = None):
    svc = _get_service(req)
    n = svc.delete_memories(user_id=user_id, agent_id=agent_id, run_id=run_id)
    return {"message": "Memories deleted successfully", "deleted": n}


@router.post("/search")
async def search(req: Request):
    body = await req.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="invalid_request")
    query = body.get("query")
    if not isinstance(query, str) or not query.strip():
        raise HTTPException(status_code=400, detail="invalid_query")
    user_id = body.get("user_id")
    agent_id = body.get("agent_id")
    run_id = body.get("run_id")
    limit = body.get("limit", 10)
    svc = _get_service(req)
    try:
        return svc.search(
            query=query,
            user_id=str(user_id) if user_id is not None else None,
            agent_id=str(agent_id) if agent_id is not None else None,
            run_id=str(run_id) if run_id is not None else None,
            limit=int(limit) if isinstance(limit, int) else 10,
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"embedder_failed: {e}")


@router.post("/reset")
async def reset(req: Request):
    body = await req.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="invalid_request")
    user_id = body.get("user_id")
    agent_id = body.get("agent_id")
    run_id = body.get("run_id")
    svc = _get_service(req)
    n = svc.delete_memories(
        user_id=str(user_id) if user_id is not None else None,
        agent_id=str(agent_id) if agent_id is not None else None,
        run_id=str(run_id) if run_id is not None else None,
    )
    return {"message": "Reset successful", "deleted": n}


@router.get("/memories/{memory_id}/history")
async def history(req: Request, memory_id: str):
    svc = _get_service(req)
    return svc.list_history(memory_id=memory_id)
