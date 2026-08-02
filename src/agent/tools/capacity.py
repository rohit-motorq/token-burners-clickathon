"""CAPACITY genre — projects concurrency forward from the recent trend to
recommend a scale up/hold/down action. ponytail: naive linear extrapolation
off get_trend's current slope, no seasonality/day-of-week model — good
enough to flag a clear ramp or fall-off; revisit if the naive slope proves
too noisy against real traffic bursts."""
from ..observability import observe
from .concurrency import get_trend

_SCALE_UP_THRESHOLD = 0.20
_SCALE_DOWN_THRESHOLD = -0.20


@observe(as_type="tool")
def predict_load(dims: dict, end: str, horizon_minutes: int = 10, lookback_minutes: int = 10) -> dict:
    trend = get_trend(dims, end, lookback_minutes)
    if trend["direction"] == "insufficient_data" or not trend["points"]:
        return {"action": "insufficient_data", "current_concurrency": None,
                "predicted_concurrency": None, "horizon_minutes": horizon_minutes}

    current = trend["points"][0]["cc"]
    slope = trend["slope_per_min"] or 0
    predicted = max(0, current + slope * horizon_minutes)
    growth_pct = None if current == 0 else (predicted - current) / current

    if growth_pct is not None and growth_pct >= _SCALE_UP_THRESHOLD:
        action = "scale_up"
    elif growth_pct is not None and growth_pct <= _SCALE_DOWN_THRESHOLD:
        action = "scale_down"
    else:
        action = "hold"

    return {
        "current_concurrency": current,
        "predicted_concurrency": round(predicted),
        "horizon_minutes": horizon_minutes,
        "slope_per_min": slope,
        "projected_growth_pct": growth_pct,
        "action": action,
        "disclaimer": "Linear projection off the recent trend, not a capacity model. Directional signal only.",
    }
