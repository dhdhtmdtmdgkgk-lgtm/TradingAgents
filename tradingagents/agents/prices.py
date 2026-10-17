"""Preserve explicit price fields without guessing numbers from narrative prose."""

import math
import re

from tradingagents.agents.schemas import _coerce_optional_float


def optional_price(value):
    value = _coerce_optional_float(value)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value > 0 else None


def price_levels(parsed=None, text="", *, adjustments=False) -> dict:
    """Structured values first; fallback accepts only uniquely labelled fields.

    PM fallback accepts 'Entry Price Adjustment' / 'Stop Loss Adjustment' only,
    never prices quoted in a thesis or the Trader's original levels.
    """
    labels = {"entry_price": "Entry Price", "stop_loss": "Stop Loss"}
    result = {"source": "structured" if parsed is not None else "labelled_text"}
    for key, label in labels.items():
        if parsed is not None:
            value = getattr(parsed, key, None)
        else:
            label += " Adjustment" if adjustments else ""
            matches = re.findall(r"^\s*(?:[-*]\s+)?\*{0,2}" + label +
                                 r"\*{0,2}\s*:\s*(.*?)\s*$", text, re.I | re.M)
            value = matches[0] if len(matches) == 1 else None
        result[key] = optional_price(value)
    return result
