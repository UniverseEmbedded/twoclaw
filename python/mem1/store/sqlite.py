import json
import os
import sqlite3
import time
import uuid
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


def _now_s() -> int:
    return int(time.time())


def _json_dumps(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)


def _json_loads(s: str) -> Any:
    return json.loads(s)


def _mk_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"

def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _scope_where(user_id: str | None, agent_id: str | None, run_id: str | None) -> tuple[str, list[Any]]:
    wh: list[str] = []
    args: list[Any] = []
    if user_id is not None:
        wh.append("user_id = ?")
        args.append(user_id)
    if agent_id is not None:
        wh.append("agent_id = ?")
        args.append(agent_id)
    if run_id is not None:
        wh.append("run_id = ?")
        args.append(run_id)
    if not wh:
        return "1=1", []
    return " AND ".join(wh), args


@dataclass
class SqliteStore:
    db_path: Path

    def __post_init__(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.db_path), check_same_thread=False)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute("PRAGMA synchronous=NORMAL;")
        return con

    def _init_db(self) -> None:
        con = self._connect()
        try:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                  id TEXT PRIMARY KEY,
                  user_id TEXT,
                  agent_id TEXT,
                  run_id TEXT,
                  memory TEXT NOT NULL,
                  metadata_json TEXT NOT NULL,
                  created_at INTEGER NOT NULL,
                  updated_at INTEGER NOT NULL
                );
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS memo_chunks (
                  id TEXT PRIMARY KEY,
                  memory_id TEXT NOT NULL,
                  chunk_index INTEGER NOT NULL,
                  chunk TEXT NOT NULL,
                  embedding_json TEXT NOT NULL,
                  created_at INTEGER NOT NULL,
                  source_path TEXT,
                  source_mtime INTEGER,
                  source_size INTEGER,
                  embedding_model TEXT
                );
                """
            )
            cols = {str(r["name"]) for r in con.execute("PRAGMA table_info(memo_chunks)").fetchall()}
            if "source_path" not in cols:
                con.execute("ALTER TABLE memo_chunks ADD COLUMN source_path TEXT;")
            if "source_mtime" not in cols:
                con.execute("ALTER TABLE memo_chunks ADD COLUMN source_mtime INTEGER;")
            if "source_size" not in cols:
                con.execute("ALTER TABLE memo_chunks ADD COLUMN source_size INTEGER;")
            if "embedding_model" not in cols:
                con.execute("ALTER TABLE memo_chunks ADD COLUMN embedding_model TEXT;")
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_history (
                  id TEXT PRIMARY KEY,
                  memory_id TEXT NOT NULL,
                  event TEXT NOT NULL,
                  old_memory TEXT,
                  new_memory TEXT,
                  created_at INTEGER NOT NULL
                );
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS ingest_state (
                  source_path TEXT PRIMARY KEY,
                  source_mtime INTEGER NOT NULL,
                  source_size INTEGER NOT NULL,
                  updated_at INTEGER NOT NULL
                );
                """
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories(user_id, agent_id, run_id);")
            con.execute("CREATE INDEX IF NOT EXISTS idx_chunks_scope ON memo_chunks(memory_id);")
            try:
                con.execute(
                    "CREATE INDEX IF NOT EXISTS idx_chunks_source ON memo_chunks(source_path, chunk_index);"
                )
            except Exception:
                pass
            con.execute("CREATE INDEX IF NOT EXISTS idx_history_mid ON memory_history(memory_id, created_at);")
            con.commit()
        finally:
            con.close()

    def get_ingest_state(self, *, source_path: str) -> dict[str, Any] | None:
        sp = str(source_path)
        con = self._connect()
        try:
            r = con.execute(
                "SELECT source_path,source_mtime,source_size,updated_at FROM ingest_state WHERE source_path = ?",
                (sp,),
            ).fetchone()
            if r is None:
                return None
            return {
                "source_path": r["source_path"],
                "source_mtime": int(r["source_mtime"]),
                "source_size": int(r["source_size"]),
                "updated_at": int(r["updated_at"]),
            }
        finally:
            con.close()

    def upsert_ingest_state(self, *, source_path: str, source_mtime: int, source_size: int) -> None:
        sp = str(source_path)
        now = _now_s()
        con = self._connect()
        try:
            con.execute(
                """
                INSERT INTO ingest_state(source_path,source_mtime,source_size,updated_at)
                VALUES(?,?,?,?)
                ON CONFLICT(source_path) DO UPDATE SET
                  source_mtime=excluded.source_mtime,
                  source_size=excluded.source_size,
                  updated_at=excluded.updated_at
                """,
                (sp, int(source_mtime), int(source_size), now),
            )
            con.commit()
        finally:
            con.close()

    def delete_by_source_path(self, *, source_path: str) -> int:
        sp = str(source_path)
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT DISTINCT memory_id FROM memo_chunks WHERE source_path = ?",
                (sp,),
            ).fetchall()
            mem_ids = [str(r["memory_id"]) for r in rows]
            if mem_ids:
                qs = ",".join(["?"] * len(mem_ids))
                con.execute(f"DELETE FROM memo_chunks WHERE memory_id IN ({qs})", mem_ids)
                con.execute(f"DELETE FROM memory_history WHERE memory_id IN ({qs})", mem_ids)
                con.execute(f"DELETE FROM memories WHERE id IN ({qs})", mem_ids)
            con.execute("DELETE FROM memo_chunks WHERE source_path = ?", (sp,))
            con.execute("DELETE FROM ingest_state WHERE source_path = ?", (sp,))
            con.commit()
            return len(mem_ids)
        finally:
            con.close()

    def replace_file_chunks(
        self,
        *,
        user_id: str | None,
        agent_id: str | None,
        run_id: str | None,
        source_path: str,
        source_mtime: int,
        source_size: int,
        embedding_model: str,
        chunks: Sequence[str],
        chunk_embeddings: Sequence[Sequence[float]],
        metadata: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        sp = str(source_path)
        now = _now_s()
        meta = dict(metadata or {})
        con = self._connect()
        try:
            old_rows = con.execute(
                "SELECT DISTINCT memory_id FROM memo_chunks WHERE source_path = ?",
                (sp,),
            ).fetchall()
            old_ids = [str(r["memory_id"]) for r in old_rows]
            if old_ids:
                qs = ",".join(["?"] * len(old_ids))
                con.execute(f"DELETE FROM memo_chunks WHERE memory_id IN ({qs})", old_ids)
                con.execute(f"DELETE FROM memory_history WHERE memory_id IN ({qs})", old_ids)
                con.execute(f"DELETE FROM memories WHERE id IN ({qs})", old_ids)

            inserted = 0
            for i, (c, e) in enumerate(zip(chunks, chunk_embeddings)):
                chash = _sha256_hex(f"{c}|{sp}|{int(i)}")
                mid = f"m_{chash[:32]}"
                cid = f"c_{chash[:32]}"
                chunk_meta = dict(meta)
                chunk_meta.update(
                    {
                        "source_path": sp,
                        "source_mtime": int(source_mtime),
                        "source_size": int(source_size),
                        "chunk_index": int(i),
                        "chunk_hash": chash,
                        "embedding_model": str(embedding_model),
                    }
                )
                con.execute(
                    "INSERT OR REPLACE INTO memories(id,user_id,agent_id,run_id,memory,metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                    (mid, user_id, agent_id, run_id, c, _json_dumps(chunk_meta), now, now),
                )
                con.execute(
                    "INSERT OR REPLACE INTO memo_chunks(id,memory_id,chunk_index,chunk,embedding_json,created_at,source_path,source_mtime,source_size,embedding_model) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        cid,
                        mid,
                        int(i),
                        c,
                        _json_dumps(list(e)),
                        now,
                        sp,
                        int(source_mtime),
                        int(source_size),
                        str(embedding_model),
                    ),
                )
                inserted += 1

            con.execute(
                """
                INSERT INTO ingest_state(source_path,source_mtime,source_size,updated_at)
                VALUES(?,?,?,?)
                ON CONFLICT(source_path) DO UPDATE SET
                  source_mtime=excluded.source_mtime,
                  source_size=excluded.source_size,
                  updated_at=excluded.updated_at
                """,
                (sp, int(source_mtime), int(source_size), now),
            )
            con.commit()
            return {
                "source_path": sp,
                "inserted": int(inserted),
                "deleted": int(len(old_ids)),
                "source_mtime": int(source_mtime),
                "source_size": int(source_size),
            }
        finally:
            con.close()

    def add_memory(
        self,
        *,
        user_id: str | None,
        agent_id: str | None,
        run_id: str | None,
        memory: str,
        metadata: Mapping[str, Any] | None,
        chunks: Sequence[str],
        chunk_embeddings: Sequence[Sequence[float]],
    ) -> dict[str, Any]:
        mid = _mk_id("m")
        now = _now_s()
        meta = dict(metadata or {})
        con = self._connect()
        try:
            con.execute(
                "INSERT INTO memories(id,user_id,agent_id,run_id,memory,metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (mid, user_id, agent_id, run_id, memory, _json_dumps(meta), now, now),
            )
            for i, (c, e) in enumerate(zip(chunks, chunk_embeddings)):
                cid = _mk_id("c")
                con.execute(
                    "INSERT INTO memo_chunks(id,memory_id,chunk_index,chunk,embedding_json,created_at,source_path,source_mtime,source_size,embedding_model) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (cid, mid, int(i), c, _json_dumps(list(e)), now, None, None, None, None),
                )
            con.commit()
        finally:
            con.close()
        return {
            "id": mid,
            "memory": memory,
            "metadata": meta,
            "created_at": now,
            "updated_at": now,
        }

    def list_memories(
        self,
        *,
        user_id: str | None,
        agent_id: str | None,
        run_id: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(1000, int(limit)))
        where, args = _scope_where(user_id, agent_id, run_id)
        con = self._connect()
        try:
            rows = con.execute(
                f"SELECT id,memory,metadata_json,created_at,updated_at FROM memories WHERE {where} ORDER BY created_at DESC LIMIT ?",
                (*args, limit),
            ).fetchall()
            return [
                {
                    "id": r["id"],
                    "memory": r["memory"],
                    "metadata": _json_loads(r["metadata_json"]),
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                }
                for r in rows
            ]
        finally:
            con.close()

    def get_memory(self, memory_id: str) -> dict[str, Any] | None:
        con = self._connect()
        try:
            r = con.execute(
                "SELECT id,user_id,agent_id,run_id,memory,metadata_json,created_at,updated_at FROM memories WHERE id = ?",
                (memory_id,),
            ).fetchone()
            if r is None:
                return None
            return {
                "id": r["id"],
                "user_id": r["user_id"],
                "agent_id": r["agent_id"],
                "run_id": r["run_id"],
                "memory": r["memory"],
                "metadata": _json_loads(r["metadata_json"]),
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
        finally:
            con.close()

    def update_memory(
        self, *, memory_id: str, memory: str | None, metadata: Mapping[str, Any] | None
    ) -> dict[str, Any] | None:
        con = self._connect()
        now = _now_s()
        try:
            cur = con.execute(
                "SELECT memory,metadata_json FROM memories WHERE id = ?",
                (memory_id,),
            ).fetchone()
            if cur is None:
                return None
            old_memory = str(cur["memory"])
            old_meta = _json_loads(cur["metadata_json"])
            new_memory = old_memory if memory is None else str(memory)
            new_meta = old_meta if metadata is None else dict(metadata)
            con.execute(
                "UPDATE memories SET memory = ?, metadata_json = ?, updated_at = ? WHERE id = ?",
                (new_memory, _json_dumps(new_meta), now, memory_id),
            )
            hid = _mk_id("h")
            con.execute(
                "INSERT INTO memory_history(id,memory_id,event,old_memory,new_memory,created_at) VALUES(?,?,?,?,?,?)",
                (hid, memory_id, "UPDATE", old_memory, new_memory, now),
            )
            con.commit()
            out = self.get_memory(memory_id)
            return out
        finally:
            con.close()

    def delete_memory(self, *, memory_id: str) -> bool:
        con = self._connect()
        try:
            con.execute("DELETE FROM memo_chunks WHERE memory_id = ?", (memory_id,))
            con.execute("DELETE FROM memory_history WHERE memory_id = ?", (memory_id,))
            cur = con.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            con.commit()
            return (cur.rowcount or 0) > 0
        finally:
            con.close()

    def delete_memories(
        self, *, user_id: str | None, agent_id: str | None, run_id: str | None
    ) -> int:
        where, args = _scope_where(user_id, agent_id, run_id)
        con = self._connect()
        try:
            mem_ids = [
                r["id"]
                for r in con.execute(f"SELECT id FROM memories WHERE {where}", args).fetchall()
            ]
            if not mem_ids:
                return 0
            for mid in mem_ids:
                con.execute("DELETE FROM memo_chunks WHERE memory_id = ?", (mid,))
                con.execute("DELETE FROM memory_history WHERE memory_id = ?", (mid,))
            cur = con.execute(f"DELETE FROM memories WHERE {where}", args)
            con.commit()
            return int(cur.rowcount or 0)
        finally:
            con.close()

    def list_history(self, *, memory_id: str, limit: int = 200) -> list[dict[str, Any]]:
        limit = max(1, min(1000, int(limit)))
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT id,memory_id,event,old_memory,new_memory,created_at FROM memory_history WHERE memory_id = ? ORDER BY created_at DESC LIMIT ?",
                (memory_id, limit),
            ).fetchall()
            return [
                {
                    "id": r["id"],
                    "memory_id": r["memory_id"],
                    "event": r["event"],
                    "old_memory": r["old_memory"],
                    "new_memory": r["new_memory"],
                    "created_at": r["created_at"],
                }
                for r in rows
            ]
        finally:
            con.close()

    def iter_chunk_rows(
        self, *, memory_ids: Iterable[str] | None = None
    ) -> Iterable[dict[str, Any]]:
        con = self._connect()
        try:
            if memory_ids is None:
                rows = con.execute(
                    "SELECT id,memory_id,chunk,embedding_json,created_at FROM memo_chunks"
                ).fetchall()
            else:
                ids = [str(x) for x in memory_ids]
                if not ids:
                    return []
                qs = ",".join(["?"] * len(ids))
                rows = con.execute(
                    f"SELECT id,memory_id,chunk,embedding_json,created_at FROM memo_chunks WHERE memory_id IN ({qs})",
                    ids,
                ).fetchall()
            for r in rows:
                yield {
                    "id": r["id"],
                    "memory_id": r["memory_id"],
                    "chunk": r["chunk"],
                    "embedding": _json_loads(r["embedding_json"]),
                    "created_at": r["created_at"],
                }
        finally:
            con.close()

    def select_memory_ids_for_scope(
        self, *, user_id: str | None, agent_id: str | None, run_id: str | None, limit: int = 5000
    ) -> list[str]:
        limit = max(1, min(20000, int(limit)))
        where, args = _scope_where(user_id, agent_id, run_id)
        con = self._connect()
        try:
            rows = con.execute(
                f"SELECT id FROM memories WHERE {where} ORDER BY created_at DESC LIMIT ?",
                (*args, limit),
            ).fetchall()
            return [str(r["id"]) for r in rows]
        finally:
            con.close()

    def counts(self) -> dict[str, int]:
        con = self._connect()
        try:
            chunk_count = int(con.execute("SELECT COUNT(1) AS c FROM memo_chunks").fetchone()["c"])
            mem_count = int(con.execute("SELECT COUNT(1) AS c FROM memories").fetchone()["c"])
            hist_count = int(con.execute("SELECT COUNT(1) AS c FROM memory_history").fetchone()["c"])
            return {"memory_count": mem_count, "chunk_count": chunk_count, "history_count": hist_count}
        finally:
            con.close()
