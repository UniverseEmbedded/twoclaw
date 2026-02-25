from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ModelPricing:
    usd_per_1k_input_tokens: float
    usd_per_1k_output_tokens: float


_DEFAULT_MODEL_PRICING: dict[str, ModelPricing] = {
    "gemini-2.5-flash": ModelPricing(usd_per_1k_input_tokens=0.005, usd_per_1k_output_tokens=0.015),
    "gemini-3-flash-preview": ModelPricing(usd_per_1k_input_tokens=0.006, usd_per_1k_output_tokens=0.018),
}


def _env_float(name: str) -> Optional[float]:
    v = os.environ.get(name)
    if v is None:
        return None
    try:
        return float(v.strip())
    except Exception:
        return None


def get_model_pricing(model: str) -> Optional[ModelPricing]:
    mp = _DEFAULT_MODEL_PRICING.get(model)
    if mp is None:
        return None
    in_over = _env_float(f"FREEPOOL_PRICING_{model.upper().replace('-', '_')}_IN_USD_PER_1K")
    out_over = _env_float(f"FREEPOOL_PRICING_{model.upper().replace('-', '_')}_OUT_USD_PER_1K")
    if in_over is None and out_over is None:
        return mp
    return ModelPricing(
        usd_per_1k_input_tokens=float(in_over if in_over is not None else mp.usd_per_1k_input_tokens),
        usd_per_1k_output_tokens=float(out_over if out_over is not None else mp.usd_per_1k_output_tokens),
    )


def estimate_cost_usd(*, model: str, prompt_tokens: int, completion_tokens: int) -> Optional[float]:
    pricing = get_model_pricing(model)
    if pricing is None:
        return None
    pt = max(0, int(prompt_tokens))
    ct = max(0, int(completion_tokens))
    return (pt / 1000.0) * pricing.usd_per_1k_input_tokens + (ct / 1000.0) * pricing.usd_per_1k_output_tokens
