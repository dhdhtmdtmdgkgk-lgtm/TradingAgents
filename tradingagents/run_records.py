"""Immutable JSON archives of completed analyses, not failed-run journals."""

import json
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

from tradingagents.agents.prices import optional_price, price_levels
from tradingagents.agents.rating import run_rating


class CompletedRunSaveError(RuntimeError):
    """The analysis completed, but its JSON could not be archived."""


def redact(value):
    """Scrub credential values and authenticated URLs even if echoed in a report."""
    secrets = [v for k, v in os.environ.items() if len(v) >= 6 and
               any(word in k.upper() for word in ("API_KEY", "TOKEN", "SECRET", "PASSWORD"))]

    def clean(item):
        if isinstance(item, str):
            for secret in secrets:
                item = item.replace(secret, "[REDACTED]")
            item = re.sub(r"(https?://)[^\s/@]+:[^\s/@]+@", r"\1[REDACTED]@", item)
            item = re.sub(r"(?i)([?&](?:api[_-]?key|token|access_token|secret|password)=)[^\s&#]+",
                          r"\1[REDACTED]", item)
            return re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/-]+=*", r"\1[REDACTED]", item)
        if isinstance(item, dict):
            return {k: clean(v) for k, v in item.items()}
        if isinstance(item, (tuple, list)):
            return [clean(v) for v in item]
        return item

    return clean(value)


def price_record(state) -> dict:
    original = state.get("trader_price_levels") or price_levels(text=state.get("trader_investment_plan", ""))
    corrections = state.get("pm_price_adjustments") or price_levels(
        text=state.get("final_trade_decision", ""), adjustments=True)
    trader = {key: optional_price(original.get(key)) for key in ("entry_price", "stop_loss")}
    pm = {key: optional_price(corrections.get(key)) for key in trader}
    for key in trader:
        if pm[key] == trader[key] or not (state.get("market_report") or "").strip():
            pm[key] = None
    return {
        "trader_original": {**trader, "source": original.get("source", "unknown")},
        "pm_adjustments": {**pm, "source": corrections.get("source", "unknown")},
        "effective_reference": {key: pm[key] if pm[key] is not None else trader[key] for key in trader},
        "rule": "PM non-null correction overrides Trader per field; otherwise retain Trader, including null. "
                "Reference prices only, not an order or executed trade.",
    }


def write_completed_run(results_dir, state, settings, *, metadata=None, usage=None) -> Path:
    """Commit one complete record atomically and never replace another run."""
    if not (state.get("final_trade_decision") or "").strip():
        raise ValueError("analysis has no final decision; no completed JSON saved")
    metadata = metadata or {}
    run_id = metadata.get("run_id") or uuid.uuid4().hex
    # IDs are generated internally; still disallow path traversal for direct callers.
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise ValueError("invalid run_id")
    fields = ("company_of_interest", "trade_date", "asset_type", "instrument_context",
              "market_report", "sentiment_report", "news_report", "fundamentals_report",
              "investment_debate_state", "investment_plan", "trader_investment_plan",
              "risk_debate_state", "final_trade_decision", "portfolio_context", "past_context", "memory_note")
    entry = {key: state.get(key) for key in fields}
    entry.update(
        schema_version=1, run_id=run_id, status="completed",
        started_at=metadata.get("started_at"), completed_at=datetime.now(UTC).isoformat(),
        final_rating=run_rating(state), run_settings=settings,
        portfolio=metadata.get("portfolio"), prices=price_record(state),
        api_usage=usage or {"status": "unavailable"},
        usage_scope="Observed calls in this invocation, including Memory settlement; "
                    "previous interrupted attempts and non-LangChain APIs are not included.",
        resumed=metadata.get("resumed", False),
    )
    # Serialise before creating a file: invalid data cannot leave a partial archive.
    text = json.dumps(redact(entry), ensure_ascii=False, indent=2, allow_nan=False)
    directory = Path(results_dir) / "runs"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{run_id}.json"
    temporary = directory / f".{run_id}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        # Atomic publication with no-overwrite semantics, also on Windows NTFS.
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path
