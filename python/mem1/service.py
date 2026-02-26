import datetime as _dt
import logging
import os
import time
import threading
from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from pathlib import Path

from .algorithms import chunk_text, cosine_similarity
from .config import Mem1Settings, RuntimeConfig
from .embedder import Embedder, GeminiEmbedder, HashEmbedder
from .store import SqliteStore


def _ts_to_iso(ts: int) -> str:
    return _dt.datetime.fromtimestamp(int(ts), tz=_dt.timezone.utc).isoformat()


def _messages_to_memory(messages: Sequence[Mapping[str, Any]]) -> str:
    parts: list[str] = []
    for m in messages:
        if not isinstance(m, Mapping):
            continue
        role = str(m.get("role") or "").strip() or "unknown"
        content = str(m.get("content") or "").strip()
        if not content:
            continue
        parts.append(f"{role}: {content}")
    return "\n".join(parts).strip()


@dataclass
class SearchHit:
    memory_id: str
    score: float


class Mem1Service:
    def __init__(self, *, settings: Mem1Settings):
        self._settings = settings
        self._store = SqliteStore(db_path=settings.db_path)
        self._config = RuntimeConfig.from_settings(settings)
        self._lock = threading.RLock()

    @property
    def settings(self) -> Mem1Settings:
        return self._settings

    @property
    def runtime_config(self) -> RuntimeConfig:
        with self._lock:
            return self._config

    def configure(self, payload: Mapping[str, Any]) -> None:
        with self._lock:
            self._config = self._config.update_from_configure_payload(payload)

    def _build_embedder(self) -> Embedder:
        cfg = self.runtime_config
        provider = cfg.embedder_provider
        if provider == "gemini":
            return GeminiEmbedder(model=cfg.embedder_model or "gemini-embedding-001")
        return HashEmbedder(model=cfg.embedder_model or "hash-v1")

    def add_memory(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        user_id: str | None,
        agent_id: str | None,
        run_id: str | None,
        metadata: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        cfg = self.runtime_config
        memory = _messages_to_memory(messages)
        embedder = self._build_embedder()
        chunks = chunk_text(memory, chunk_size=cfg.chunk_size, overlap=cfg.chunk_overlap)
        embeddings = embedder.embed_texts(chunks) if chunks else []
        item = self._store.add_memory(
            user_id=user_id,
            agent_id=agent_id,
            run_id=run_id,
            memory=memory,
            metadata=metadata,
            chunks=chunks,
            chunk_embeddings=embeddings,
        )
        return {
            "id": item["id"],
            "memory": item["memory"],
            "metadata": item["metadata"],
            "created_at": _ts_to_iso(item["created_at"]),
            "updated_at": _ts_to_iso(item["updated_at"]),
            "embedder": embedder.model_name,
            "chunk_count": len(chunks),
        }

    def ingest_files(
        self,
        *,
        source_path: str,
        user_id: str | None,
        agent_id: str | None,
        run_id: str | None,
        incremental: bool,
    ) -> dict[str, Any]:
        log = logging.getLogger("mem1.ingest")
        sp = str(source_path or "").strip()
        if not sp:
            raise ValueError("invalid_source_path")
        root = Path(sp)
        if not root.exists():
            raise FileNotFoundError(sp)

        cfg = self.runtime_config
        embedder = self._build_embedder()
        deadline_s = None
        raw_timeout = (os.environ.get("MEM1_INGEST_TIMEOUT_SECS") or "").strip()
        if raw_timeout:
            try:
                deadline_s = time.monotonic() + max(1, int(raw_timeout))
            except Exception:
                deadline_s = None
        files: list[Path] = []
        if root.is_file():
            files = [root]
        else:
            for p in root.rglob("*"):
                if not p.is_file():
                    continue
                if p.suffix.lower() not in {".md", ".txt"}:
                    continue
                files.append(p)

        chunks_added = 0
        chunks_skipped = 0
        errors: list[dict[str, Any]] = []
        ordered = sorted(files)
        log.warning("ingest start: root=%s files=%d incremental=%s embedder=%s", str(root), len(ordered), incremental, embedder.model_name)
        for idx, fp in enumerate(ordered, start=1):
            if deadline_s is not None and time.monotonic() > deadline_s:
                errors.append({"source_path": str(fp), "error": "ingest_timeout"})
                log.warning("ingest timeout reached, stopping (processed=%d/%d)", idx - 1, len(ordered))
                break
            try:
                st = fp.stat()
                mtime = int(st.st_mtime)
                size = int(st.st_size)
                sp_file = str(fp)
                if incremental:
                    prev = self._store.get_ingest_state(source_path=sp_file)
                    if prev and int(prev.get("source_mtime") or 0) == mtime and int(prev.get("source_size") or 0) == size:
                        chunks_skipped += 1
                        if idx == 1 or idx % 10 == 0:
                            log.warning("ingest progress: %d/%d skipped=%d added=%d", idx, len(ordered), chunks_skipped, chunks_added)
                        continue

                text = fp.read_text(encoding="utf-8", errors="ignore")
                chunks = chunk_text(text, chunk_size=cfg.chunk_size, overlap=cfg.chunk_overlap)
                if not chunks:
                    self._store.delete_by_source_path(source_path=sp_file)
                    self._store.upsert_ingest_state(source_path=sp_file, source_mtime=mtime, source_size=size)
                    continue
                log.warning("ingest file: %d/%d path=%s chunks=%d", idx, len(ordered), sp_file, len(chunks))
                embeddings = embedder.embed_texts(chunks)
                res = self._store.replace_file_chunks(
                    user_id=user_id,
                    agent_id=agent_id,
                    run_id=run_id,
                    source_path=sp_file,
                    source_mtime=mtime,
                    source_size=size,
                    embedding_model=embedder.model_name,
                    chunks=chunks,
                    chunk_embeddings=embeddings,
                    metadata={"source_type": "file_ingest", "root_path": str(root)},
                )
                chunks_added += int(res.get("inserted") or 0)
                if idx == 1 or idx % 10 == 0:
                    log.warning("ingest progress: %d/%d skipped=%d added=%d", idx, len(ordered), chunks_skipped, chunks_added)
            except Exception as e:
                errors.append({"source_path": str(fp), "error": str(e)})
                log.warning("ingest error: path=%s err=%s", str(fp), str(e))

        log.warning("ingest done: root=%s skipped=%d added=%d errors=%d", str(root), chunks_skipped, chunks_added, len(errors))
        return {
            "source_path": str(root),
            "files": len(files),
            "chunks_added": int(chunks_added),
            "chunks_skipped": int(chunks_skipped),
            "errors": errors,
            "embedder": embedder.model_name,
        }

    def list_memories(
        self, *, user_id: str | None, agent_id: str | None, run_id: str | None, limit: int
    ) -> list[dict[str, Any]]:
        items = self._store.list_memories(user_id=user_id, agent_id=agent_id, run_id=run_id, limit=limit)
        out: list[dict[str, Any]] = []
        for it in items:
            out.append(
                {
                    "id": it["id"],
                    "memory": it["memory"],
                    "metadata": it["metadata"],
                    "created_at": _ts_to_iso(it["created_at"]),
                }
            )
        return out

    def get_memory(self, memory_id: str) -> dict[str, Any] | None:
        it = self._store.get_memory(memory_id)
        if it is None:
            return None
        return {
            "id": it["id"],
            "memory": it["memory"],
            "metadata": it["metadata"],
            "created_at": _ts_to_iso(it["created_at"]),
            "updated_at": _ts_to_iso(it["updated_at"]),
        }

    def update_memory(
        self, *, memory_id: str, memory: str | None, metadata: Mapping[str, Any] | None
    ) -> dict[str, Any] | None:
        it = self._store.update_memory(memory_id=memory_id, memory=memory, metadata=metadata)
        if it is None:
            return None
        return {
            "id": it["id"],
            "memory": it["memory"],
            "metadata": it["metadata"],
            "created_at": _ts_to_iso(it["created_at"]),
            "updated_at": _ts_to_iso(it["updated_at"]),
        }

    def delete_memory(self, *, memory_id: str) -> bool:
        return self._store.delete_memory(memory_id=memory_id)

    def delete_memories(self, *, user_id: str | None, agent_id: str | None, run_id: str | None) -> int:
        return self._store.delete_memories(user_id=user_id, agent_id=agent_id, run_id=run_id)

    def list_history(self, *, memory_id: str) -> list[dict[str, Any]]:
        items = self._store.list_history(memory_id=memory_id)
        out: list[dict[str, Any]] = []
        for it in items:
            out.append(
                {
                    "id": it["id"],
                    "memory_id": it["memory_id"],
                    "event": it["event"],
                    "old_memory": it["old_memory"],
                    "new_memory": it["new_memory"],
                    "created_at": _ts_to_iso(it["created_at"]),
                }
            )
        return out

    def search(
        self,
        *,
        query: str,
        user_id: str | None,
        agent_id: str | None,
        run_id: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        q = str(query or "").strip()
        if not q:
            return []
        limit = max(1, min(100, int(limit)))
        embedder = self._build_embedder()
        q_emb = embedder.embed_texts([q])[0]

        mids = self._store.select_memory_ids_for_scope(user_id=user_id, agent_id=agent_id, run_id=run_id)
        best: dict[str, float] = {}
        for row in self._store.iter_chunk_rows(memory_ids=mids):
            score = cosine_similarity(q_emb, row["embedding"])
            mid = str(row["memory_id"])
            cur = best.get(mid)
            if cur is None or score > cur:
                best[mid] = float(score)

        ranked = sorted(best.items(), key=lambda x: x[1], reverse=True)[:limit]
        out: list[dict[str, Any]] = []
        for mid, score in ranked:
            it = self._store.get_memory(mid)
            if it is None:
                continue
            out.append(
                {
                    "id": it["id"],
                    "memory": it["memory"],
                    "score": float(score),
                    "metadata": it["metadata"],
                    "created_at": _ts_to_iso(it["created_at"]),
                }
            )
        return out

    def status(self) -> dict[str, Any]:
        cfg = self.runtime_config
        counts = self._store.counts()
        return {
            "db_path": str(self._settings.db_path),
            "chunk_count": int(counts["chunk_count"]),
            "tag_count": 0,
            "index_ready": True,
            "embedder": cfg.embedder_model,
            "rag_params_version": cfg.rag_params_version,
        }
