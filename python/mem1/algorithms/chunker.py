from __future__ import annotations


def chunk_text(text: str, *, chunk_size: int, overlap: int) -> list[str]:
    s = str(text or "")
    if chunk_size <= 0:
        return [s]
    if overlap < 0:
        overlap = 0
    if not s.strip():
        return []
    out: list[str] = []
    start = 0
    n = len(s)
    while start < n:
        end = min(n, start + chunk_size)
        chunk = s[start:end].strip()
        if chunk:
            out.append(chunk)
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return out
