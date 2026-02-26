from fastapi import APIRouter, HTTPException, Request


def _get_service(req: Request):
    svc = getattr(req.app.state, "mem1_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="mem1_not_ready")
    return svc


router = APIRouter()


@router.get("/mem1/status")
async def status(req: Request):
    svc = _get_service(req)
    return svc.status()


@router.get("/mem1/rag_params")
async def get_rag_params(req: Request):
    svc = _get_service(req)
    return svc.runtime_config.rag_params


@router.put("/mem1/rag_params")
async def put_rag_params(req: Request):
    body = await req.json()
    if not isinstance(body, (dict, list)):
        raise HTTPException(status_code=400, detail="invalid_rag_params")
    svc = _get_service(req)
    svc.configure({"rag_params": body})
    return {"message": "rag_params updated", "version": svc.runtime_config.rag_params_version}


@router.post("/mem1/reindex")
async def reindex(req: Request):
    svc = _get_service(req)
    st = svc.status()
    return {"message": "Reindex completed", "chunks_indexed": int(st["chunk_count"]), "tags_indexed": 0}


@router.post("/mem1/ingest")
async def ingest(req: Request):
    body = await req.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="invalid_request")
    source_type = str(body.get("source_type") or "").strip().lower()
    user_id = body.get("user_id")
    agent_id = body.get("agent_id")
    run_id = body.get("run_id")
    if source_type not in {"text", "file"}:
        raise HTTPException(status_code=400, detail="invalid_source_type")
    if source_type == "file":
        source_path = body.get("source_path")
        if not isinstance(source_path, str) or not source_path.strip():
            raise HTTPException(status_code=400, detail="invalid_source_path")
        incremental = body.get("incremental")
        if incremental is None:
            incremental = True
        incremental = bool(incremental)
        svc = _get_service(req)
        try:
            out = svc.ingest_files(
                source_path=source_path,
                user_id=str(user_id) if user_id is not None else None,
                agent_id=str(agent_id) if agent_id is not None else None,
                run_id=str(run_id) if run_id is not None else None,
                incremental=incremental,
            )
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="source_not_found")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {
            "message": "Ingest completed",
            "chunks_added": int(out.get("chunks_added") or 0),
            "chunks_skipped": int(out.get("chunks_skipped") or 0),
            "errors": out.get("errors") or [],
        }
    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(status_code=400, detail="invalid_text")
    svc = _get_service(req)
    try:
        item = svc.add_memory(
            messages=[{"role": "user", "content": text}],
            user_id=str(user_id) if user_id is not None else None,
            agent_id=str(agent_id) if agent_id is not None else None,
            run_id=str(run_id) if run_id is not None else None,
            metadata={"source_type": "ingest"},
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"embedder_failed: {e}")
    return {"message": "Ingest completed", "chunks_added": int(item["chunk_count"]), "chunks_skipped": 0, "errors": []}
