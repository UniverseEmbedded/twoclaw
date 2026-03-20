import hashlib
import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on"}


def _default_data_dir() -> Path:
    home = Path.home()
    return home / ".zeroclaw" / "mem1"


def _sha1_json(data: Any) -> str:
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    h = hashlib.sha1(payload).hexdigest()
    return f"sha1:{h}"


def _load_json_file(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


@dataclass(frozen=True)
class Mem1Settings:
    host: str
    port: int
    debug: bool
    db_path: Path
    rag_params_path: Path
    embedder_provider: str
    embedder_model: str
    gemini_api_key: str
    chunk_size: int
    chunk_overlap: int
    router_base_url: str
    proxy_timeout_secs: int

    @classmethod
    def from_env(cls) -> "Mem1Settings":
        base_dir = Path(os.environ.get("MEM1_DATA_DIR") or _default_data_dir())
        db_path = Path(os.environ.get("MEM1_DB_PATH") or (base_dir / "memories.db"))
        rag_path = Path(
            os.environ.get("MEM1_RAG_PARAMS_PATH")
            or (Path(__file__).parent / "default_rag_params.json")
        )
        return cls(
            host=os.environ.get("MEM1_HOST") or "127.0.0.1",
            port=int(os.environ.get("MEM1_PORT") or "8001"),
            debug=_env_bool("MEM1_DEBUG", False),
            db_path=db_path,
            rag_params_path=rag_path,
            embedder_provider=(os.environ.get("MEM1_EMBEDDER_PROVIDER") or "hash").strip().lower(),
            embedder_model=(os.environ.get("MEM1_EMBEDDER_MODEL") or "hash-v1").strip(),
            gemini_api_key=os.environ.get("MEM1_GEMINI_API_KEY") or "",
            chunk_size=int(os.environ.get("MEM1_CHUNK_SIZE") or "500"),
            chunk_overlap=int(os.environ.get("MEM1_CHUNK_OVERLAP") or "50"),
            router_base_url=(os.environ.get("MEM1_ROUTER_BASE_URL") or "http://127.0.0.1:8000").strip(),
            proxy_timeout_secs=int(os.environ.get("MEM1_PROXY_TIMEOUT_SECS") or "120"),
        )


@dataclass(frozen=True)
class RuntimeConfig:
    embedder_provider: str
    embedder_model: str
    rag_params: Any
    rag_params_version: str
    chunk_size: int
    chunk_overlap: int

    @classmethod
    def from_settings(cls, s: Mem1Settings) -> "RuntimeConfig":
        rag = _load_json_file(s.rag_params_path)
        return cls(
            embedder_provider=s.embedder_provider,
            embedder_model=s.embedder_model,
            rag_params=rag,
            rag_params_version=_sha1_json(rag),
            chunk_size=s.chunk_size,
            chunk_overlap=s.chunk_overlap,
        )

    def update_from_configure_payload(self, payload: Mapping[str, Any]) -> "RuntimeConfig":
        embedder = payload.get("embedder") if isinstance(payload.get("embedder"), dict) else {}
        rag_params = payload.get("rag_params")
        out = self
        if isinstance(embedder, dict):
            provider = embedder.get("provider")
            model = embedder.get("model")
            if isinstance(provider, str) and provider.strip():
                out = replace(out, embedder_provider=provider.strip().lower())
            if isinstance(model, str) and model.strip():
                out = replace(out, embedder_model=model.strip())
        if isinstance(rag_params, (dict, list)):
            out = replace(out, rag_params=rag_params, rag_params_version=_sha1_json(rag_params))
        return out
