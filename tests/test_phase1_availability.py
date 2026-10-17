"""The run stops for absent prices, but retains useful partial research."""

from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from tradingagents.dataflows import availability, router
from tradingagents.dataflows.config import set_config
from tradingagents.dataflows.errors import NoMarketDataError

CSV = "Date,Close\n2026-01-08,100.5\n"


@pytest.mark.parametrize("csv,valid", [
    (CSV, True), ("# header\n\n" + CSV, True),
    ("timestamp,close\n2026-01-08,100\n", True),
    ("Date,Open,Close\n2026-01-08,,100\n", True),
    ("Date,Close\n2026-01-08,\n", False),
    ("Date,Close\n2026-01-08,inf\n", False),
    ("Date,Close\n2026-01-08,-1\n", False),
    ("Date,Close\n2026-02-08,100\n", False),
    ("Date,Close\nnot-a-date,100\n", False),
    ("NO_DATA_AVAILABLE: no prices", False), ("", False),
])
def test_price_csv_availability(csv, valid):
    assert availability.has_price_rows(csv, "2026-01-01", "2026-01-09") is valid


def test_price_check_falls_back_only_within_configured_chain(monkeypatch):
    set_config({"data_vendors": {"core_stock_apis": "yfinance,alpha_vantage"}})
    primary, secondary = Mock(return_value="Date,Close\n"), Mock(return_value=CSV)
    monkeypatch.setitem(router.VENDOR_METHODS, "get_stock_data", {"yfinance": primary, "alpha_vantage": secondary})
    availability.require_price_data({"company_of_interest": "NVDA", "trade_date": "2026-01-09"})
    primary.assert_called_once()
    secondary.assert_called_once()
    set_config({"data_vendors": {"core_stock_apis": "yfinance"}})
    with pytest.raises(NoMarketDataError, match="core price"):
        availability.require_price_data({"company_of_interest": "NVDA", "trade_date": "2026-01-09"})
    assert secondary.call_count == 1


@pytest.mark.parametrize("method,category", [
    ("get_news", "news_data"), ("get_balance_sheet", "fundamental_data"),
    ("get_indicators", "technical_indicators"), ("get_macro_indicators", "macro_data"),
])
def test_partial_data_failure_is_visible_and_nonfatal(monkeypatch, method, category):
    set_config({"data_vendors": {category: "default"}})
    monkeypatch.setitem(router.VENDOR_METHODS, method, {"offline": Mock(side_effect=TimeoutError("unreachable"))})
    assert "DATA_UNAVAILABLE" in router.route_to_vendor(method, "NVDA")


def test_cli_failure_and_cancel_show_error_without_json(monkeypatch, tmp_path):
    from cli import main, run
    monkeypatch.setattr(run, "run_analysis", Mock())
    for error, code in [(NoMarketDataError("NVDA"), 1), (KeyboardInterrupt(), 130)]:
        monkeypatch.setattr(main, "run_analysis", Mock(side_effect=error))
        result = CliRunner().invoke(main.app, [])
        assert result.exit_code == code
        assert "failed" in result.output or "cancelled" in result.output
    assert not list(tmp_path.rglob("*.json"))
