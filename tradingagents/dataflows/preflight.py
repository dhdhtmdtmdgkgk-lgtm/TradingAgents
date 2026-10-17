"""Explicit, LLM-free data readiness check; never starts an analysis or changes vendors.

Run from the project directory: python -m tradingagents.dataflows.preflight
Optional-source failures are warnings. Missing core prices gives exit status 1.
"""

import argparse
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

import yfinance

from tradingagents.dataflows.availability import has_price_rows
from tradingagents.dataflows.config import run_config
from tradingagents.dataflows.date_window import get_current_date
from tradingagents.dataflows.router import route_to_vendor
from tradingagents.dataflows.vendors.reddit import fetch_reddit_posts, subreddits_for
from tradingagents.dataflows.vendors.stocktwits import fetch_stocktwits_messages
from tradingagents.dataflows.vendors.yahoo.snapshot import build_verified_market_snapshot
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.run_records import redact


def classify(text):
    """Never mistake a vendor's missing-data notice for a successful data read."""
    if not isinstance(text, str) or not text.strip():
        return "no_data"
    lower = text.lower().strip()
    if lower.startswith(("data_unavailable", "no_data_available", "<stocktwits unavailable",
                         "<reddit unavailable", "<yahoo finance news unavailable",
                         "polymarket data is currently unavailable", "prediction-market odds are withheld")):
        return "unavailable"
    if re.search(r"(?m)^(?:profile fundamentals are|insider transactions are|[^\n]+ data is) withheld for this date\.", lower):
        return "unavailable"
    if lower.startswith(("fred:", "fred series '")):
        return "unavailable"
    if re.search(r"(?m)^<?no (?:observations|open prediction markets|news found|global news found|"
                 r"articles|stocktwits messages|reddit posts|posts|insider transactions)", lower):
        return "no_data"
    return "ok"


def _checks(ticker, date):
    start = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=30)).strftime("%Y-%m-%d")
    week = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")
    return [
        ("core_prices", True, lambda: route_to_vendor("get_stock_data", ticker, start, date),
         lambda text: has_price_rows(text, start, date)),
        ("yahoo_verified_snapshot", False, lambda: build_verified_market_snapshot(ticker, date), None),
        ("technical_indicator_rsi", False, lambda: route_to_vendor("get_indicators", ticker, "rsi", date, 7), None),
        ("fundamentals", False, lambda: route_to_vendor("get_fundamentals", ticker, date), None),
        ("balance_sheet", False, lambda: route_to_vendor("get_balance_sheet", ticker, "quarterly", date), None),
        ("income_statement", False, lambda: route_to_vendor("get_income_statement", ticker, "quarterly", date), None),
        ("cashflow", False, lambda: route_to_vendor("get_cashflow", ticker, "quarterly", date), None),
        ("insider_transactions", False, lambda: route_to_vendor("get_insider_transactions", ticker, date), None),
        ("ticker_news", False, lambda: route_to_vendor("get_news", ticker, week, date), None),
        ("global_news", False, lambda: route_to_vendor("get_global_news", date, 7, 5), None),
        ("stocktwits", False, lambda: fetch_stocktwits_messages(ticker, start_date=week, end_date=date), None),
        ("reddit", False, lambda: fetch_reddit_posts(ticker, subreddits_for(ticker), start_date=week, end_date=date), None),
        *[(name, False, lambda indicator=name: route_to_vendor("get_macro_indicators", indicator, date, 365), None)
          for name in ("fed_funds_rate", "10y_treasury", "cpi")],
        ("polymarket", False, lambda: route_to_vendor("get_prediction_markets", "Fed rate decision", 3, date), None),
    ]


def check_data(ticker, date, config=None):
    """Sequential checks keep this probe from amplifying cold-cache fan-out.

    The snapshot warms the existing configured Yahoo cache for later indicators;
    it does not promise future uptime, alter prices, or certify analyst outputs.
    TypeSafe/Jev screening is deliberately not invoked, even when configured.
    """
    cfg = dict(config or DEFAULT_CONFIG)
    canonical = datetime.strptime(date, "%Y-%m-%d").strftime("%Y-%m-%d")
    if canonical != date or date > get_current_date():
        raise ValueError("date must be YYYY-MM-DD and no later than today")
    ticker = ticker.strip().upper()
    if not ticker:
        raise ValueError("ticker is required")
    results = []
    with run_config(cfg):
        for name, required, probe, validate in _checks(ticker, date):
            if name in ("fed_funds_rate", "10y_treasury", "cpi"):
                # Check format locally before transmitting any credential.
                key = os.getenv("FRED_API_KEY", "")
                if not key or not re.fullmatch(r"[a-z0-9]{32}", key):
                    results.append({"source": name, "required": required,
                                    "status": "not_configured" if not key else "invalid_key_format"})
                    continue
            try:
                text = probe()
                status = classify(text)
                if validate is not None:
                    status = "ok" if validate(text) else "unavailable"
                # Limited non-secret context: header/latest macro value, never URLs or raw errors.
                detail = next((line for line in text.splitlines() if line.startswith("**Latest:**")), "")
                results.append({"source": name, "required": required, "status": status,
                                **({"latest": detail} if detail else {})})
            except Exception as exc:
                results.append({"source": name, "required": required, "status": "unavailable",
                                "error_type": type(exc).__name__})
    return redact({
        "ticker": ticker, "analysis_date": date, "llm_calls": 0,
        "core_ready": all(r["status"] == "ok" for r in results if r["required"]),
        "checks": results,
        "configured_vendors": cfg.get("data_vendors", {}),
        "tool_overrides": cfg.get("tool_vendors", {}),
        "inactive_or_paid_services": {
            "alpha_vantage_key_present": bool(os.getenv("ALPHA_VANTAGE_API_KEY")),
            "typesafe_key_present": bool(os.getenv("TYPESAFE_API_KEY")),
            "typesafe_screening": "not_called: may incur LLM charges",
        },
        "scope": "Point-in-time readiness only; optional failures do not block core_ready. No analysis, Memory settlement or LLM inference.",
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticker", default="NVDA")
    parser.add_argument("--date", default=get_current_date())
    parser.add_argument("--cache-dir", help="Optional writable cache location")
    args = parser.parse_args()
    cfg = dict(DEFAULT_CONFIG)
    if args.cache_dir:
        cfg["data_cache_dir"] = args.cache_dir
        yfinance.set_tz_cache_location(str(Path(args.cache_dir) / "yfinance"))
    result = check_data(args.ticker, args.date, cfg)
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if result["core_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
