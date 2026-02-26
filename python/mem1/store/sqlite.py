import json
import os
import sqlite3
import time
import uuid
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
                  created_at INTEGER NOT NULL
                );
                """
            )
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
            con.execute("CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories(user_id, agent_id, run_id);")
            con.execute("CREATE INDEX IF NOT EXISTS idx_chunks_scope ON memo_chunks(memory_id);")
            con.execute("CREATE INDEX IF NOT EXISTS idx_history_mid ON memory_history(memory_id, created_at);")
            con.commit()
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
                    "INSERT INTO memo_chunks(id,memory_id,chunk_index,chunk,embedding_json,created_at) VALUES(?,?,?,?,?,?)",
                    (cid, mid, int(i), c, _json_dumps(list(e)), now),
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
