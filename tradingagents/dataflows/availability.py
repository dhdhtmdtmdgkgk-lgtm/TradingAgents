"""A prerequisite check, without changing the vendors' price or candle policy."""

from datetime import datetime, timedelta
from io import StringIO

import numpy as np
import pandas as pd

from tradingagents.dataflows.errors import NoMarketDataError
from tradingagents.dataflows.router import route_to_vendor


def has_price_rows(text: str, start: str, end: str) -> bool:
    """Understand the existing Yahoo/Alpha Vantage CSV shapes, not LLM prose.

    One finite positive close dated inside the requested range is sufficient.
    This deliberately does not introduce a session-close or minimum-history rule.
    """
    if not isinstance(text, str) or not text.strip():
        return False
    try:
        frame = pd.read_csv(StringIO(text), comment="#")
    except (pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeError):
        return False
    columns = {str(c).strip().lower(): c for c in frame.columns}
    close = columns.get("close")
    if close is None or frame.empty:
        return False
    # Yahoo history CSV has its date in the first (occasionally unnamed) column.
    date = next((columns[k] for k in ("date", "datetime", "timestamp", "time") if k in columns),
                frame.columns[0])
    dates = pd.to_datetime(frame[date], errors="coerce", utc=True).dt.normalize()
    closes = pd.to_numeric(frame[close], errors="coerce")
    return bool((dates.between(pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC"))
                 & np.isfinite(closes) & (closes > 0)).any())


def require_price_data(state) -> dict:
    """Run before analysts/settlement, including when market analysis is unselected.

    Uses the configured price chain and its existing daily CSV unchanged. This
    single additional data request does not invoke an LLM or enter agent prompts.
    """
    ticker, end = state["company_of_interest"], state["trade_date"]
    start = (datetime.strptime(end, "%Y-%m-%d") - timedelta(days=30)).strftime("%Y-%m-%d")
    result = route_to_vendor("get_stock_data", ticker, start, end,
                             _validate=lambda value: has_price_rows(value, start, end))
    if not has_price_rows(result, start, end):
        raise NoMarketDataError(ticker, detail="core price data unavailable; analysis stopped")
    return {}
