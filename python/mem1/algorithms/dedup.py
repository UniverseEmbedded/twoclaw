import math
from typing import Any, Sequence

from .similarity import cosine_similarity


def _to_vector(v: Any) -> list[float]:
    if not isinstance(v, list):
        return []
    out: list[float] = []
    for x in v:
        try:
            out.append(float(x))
        except Exception:
            return []
    return out


def _norm(v: Sequence[float]) -> float:
    s = 0.0
    for x in v:
        s += float(x) * float(x)
    return math.sqrt(s)


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    n = min(len(a), len(b))
    s = 0.0
    for i in range(n):
        s += float(a[i]) * float(b[i])
    return s


def _normalize(v: Sequence[float]) -> list[float]:
    n = _norm(v)
    if n <= 1e-12:
        return []
    return [float(x) / n for x in v]


def _project_residual(v: Sequence[float], basis: Sequence[Sequence[float]]) -> list[float]:
    if not basis:
        return [float(x) for x in v]
    out = [float(x) for x in v]
    for b in basis:
        c = _dot(out, b)
        m = min(len(out), len(b))
        for i in range(m):
            out[i] -= c * float(b[i])
    return out


def _score_range(values: Sequence[float]) -> tuple[float, float]:
    if not values:
        return (0.0, 1.0)
    lo = min(values)
    hi = max(values)
    if hi <= lo:
        return (lo, lo + 1.0)
    return (lo, hi)


def _score_norm(v: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 0.0
    return (v - lo) / (hi - lo)


def deduplicate_results(
    candidates: Sequence[dict],
    *,
    query_vector: Sequence[float] | None = None,
    top_k: int,
    threshold: float,
) -> list[dict]:
    k = max(0, int(top_k))
    if k <= 0:
        return []
    thr = max(-1.0, min(1.0, float(threshold)))
    qn = _normalize(query_vector or [])

    items: list[dict[str, Any]] = []
    for i, c in enumerate(candidates):
        if not isinstance(c, dict):
            continue
        emb = _normalize(_to_vector(c.get("embedding")))
        score = float(c.get("score") or 0.0)
        items.append({"idx": i, "item": dict(c), "embedding": emb, "score": score})
    if not items:
        return []

    lo, hi = _score_range([it["score"] for it in items])
    selected: list[int] = []
    basis: list[list[float]] = []

    while len(selected) < k:
        best_idx = -1
        best_rank = -1e18
        for i, it in enumerate(items):
            if i in selected:
                continue
            emb = it["embedding"]
            score_n = _score_norm(it["score"], lo, hi)
            if not emb:
                max_sim = -1.0
                novelty = 0.0
                qsim = 0.0
            else:
                max_sim = -1.0
                for j in selected:
                    s_emb = items[j]["embedding"]
                    if not s_emb:
                        continue
                    sim = cosine_similarity(emb, s_emb)
                    if sim > max_sim:
                        max_sim = sim
                residual = _project_residual(emb, basis)
                novelty = _norm(residual)
                qsim = _dot(emb, qn) if qn else 0.0
            if selected and max_sim >= thr and novelty <= max(0.0, 1.0 - thr):
                continue
            rank = novelty * 0.65 + qsim * 0.25 + score_n * 0.10
            if rank > best_rank:
                best_rank = rank
                best_idx = i

        if best_idx < 0:
            break
        selected.append(best_idx)
        chosen_emb = items[best_idx]["embedding"]
        if chosen_emb:
            residual = _project_residual(chosen_emb, basis)
            rn = _norm(residual)
            if rn > 1e-8:
                basis.append([x / rn for x in residual])

    if len(selected) < k:
        for i, _ in sorted(enumerate(items), key=lambda x: x[1]["score"], reverse=True):
            if i in selected:
                continue
            selected.append(i)
            if len(selected) >= k:
                break

    return [items[i]["item"] for i in selected[:k]]
