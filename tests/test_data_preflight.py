"""Data-only readiness: missing optional inputs must not look like a ready feed."""
import json

import pytest

from tradingagents.dataflows import preflight


@pytest.mark.unit
@pytest.mark.parametrize("text,expected", [
    ("DATA_UNAVAILABLE: network timeout", "unavailable"),
    ("<stocktwits unavailable: HTTPError>", "unavailable"),
    ("No observations in this window", "no_data"),
    ("No open prediction markets matched", "no_data"),
    ("", "no_data"),
    ("## data\n2026-10-16: 214.13", "ok"),
    ("## News\n### Chips unavailable in some markets", "ok"),
    ("No insider transactions reported for symbol NVDA", "no_data"),
    ("# Company Fundamentals for NVDA\n\nProfile fundamentals are withheld for this date.", "unavailable"),
    ("# Insider Transactions\n\nInsider transactions are withheld for this date.", "unavailable"),
    ("# Balance Sheet\n\nBalance Sheet data is withheld for this date.", "unavailable"),
    ("FRED series 'UNKNOWN' not found.", "unavailable"),
])
def test_status_distinguishes_absence_from_valid_data(text, expected):
    assert preflight.classify(text) == expected


@pytest.mark.unit
def test_optional_failure_is_reported_but_only_core_failure_blocks(monkeypatch):
    monkeypatch.setattr(preflight, "get_current_date", lambda: "2026-10-17")
    monkeypatch.setattr(preflight, "_checks", lambda *a: [
        ("core_prices", True, lambda: "valid", lambda s: s == "valid"),
        ("optional", False, lambda: "DATA_UNAVAILABLE: failed", None),
    ])
    result = preflight.check_data("NVDA", "2026-10-17")
    assert result["core_ready"] and result["llm_calls"] == 0
    assert result["checks"][1]["status"] == "unavailable"
    monkeypatch.setattr(preflight, "_checks", lambda *a: [
        ("core_prices", True, lambda: "not prices", lambda s: False),
    ])
    assert not preflight.check_data("NVDA", "2026-10-17")["core_ready"]


@pytest.mark.unit
@pytest.mark.parametrize("key,status", [("", "not_configured"), ("bad\x16key", "invalid_key_format")])
def test_fred_key_format_is_checked_before_transmission(monkeypatch, key, status):
    monkeypatch.setattr(preflight, "get_current_date", lambda: "2026-10-17")
    monkeypatch.setenv("FRED_API_KEY", key)
    def no_request():
        pytest.fail("invalid or missing key must never be transmitted")
    monkeypatch.setattr(preflight, "_checks", lambda *a: [("cpi", False, no_request, None)])
    assert preflight.check_data("NVDA", "2026-10-17")["checks"][0]["status"] == status


@pytest.mark.unit
def test_vendor_exception_never_prints_credential_or_authenticated_url(monkeypatch):
    monkeypatch.setattr(preflight, "get_current_date", lambda: "2026-10-17")
    monkeypatch.setenv("FRED_API_KEY", "a" * 32)
    def fail():
        raise ValueError("https://example.invalid?api_key=" + "a" * 32)
    monkeypatch.setattr(preflight, "_checks", lambda *a: [("cpi", False, fail, None)])
    result = preflight.check_data("NVDA", "2026-10-17")
    assert "a" * 32 not in json.dumps(result)
    assert result["checks"][0]["error_type"] == "ValueError"


@pytest.mark.unit
def test_future_date_fails_before_data_requests(monkeypatch):
    monkeypatch.setattr(preflight, "get_current_date", lambda: "2026-10-17")
    monkeypatch.setattr(preflight, "_checks", lambda *a: pytest.fail("no request expected"))
    with pytest.raises(ValueError, match="date"):
        preflight.check_data("NVDA", "2026-10-18")


@pytest.mark.unit
def test_cli_core_not_ready_returns_nonzero(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["preflight", "--ticker", "NVDA"])
    monkeypatch.setattr(preflight, "check_data", lambda *a: {"core_ready": False, "llm_calls": 0})
    assert preflight.main() == 1
    assert json.loads(capsys.readouterr().out)["llm_calls"] == 0


@pytest.mark.unit
def test_real_checks_preserve_insider_analysis_date(monkeypatch):
    calls = []
    monkeypatch.setattr(preflight, "route_to_vendor", lambda *args: calls.append(args) or "data")
    checks = {name: probe for name, _, probe, _ in preflight._checks("NVDA", "2026-09-01")}
    checks["insider_transactions"]()
    assert calls == [("get_insider_transactions", "NVDA", "2026-09-01")]


@pytest.mark.unit
def test_real_probe_path_does_not_call_llm_or_mutate_config(monkeypatch):
    from copy import deepcopy

    from tradingagents.dataflows.config import get_config

    before = get_config()
    defaults_before = deepcopy(preflight.DEFAULT_CONFIG)
    monkeypatch.setattr(preflight, "get_current_date", lambda: "2026-10-17")
    monkeypatch.setenv("FRED_API_KEY", "a" * 32)
    prices = "Date,Close\n2026-10-16,214.13\n"
    monkeypatch.setattr(preflight, "route_to_vendor", lambda method, *args:
                        prices if method == "get_stock_data" else "valid data")
    monkeypatch.setattr(preflight, "build_verified_market_snapshot", lambda *args: "valid snapshot")
    monkeypatch.setattr(preflight, "fetch_stocktwits_messages", lambda *args, **kwargs: "valid social")
    monkeypatch.setattr(preflight, "fetch_reddit_posts", lambda *args, **kwargs: "valid social")
    import openai
    monkeypatch.setattr(openai.OpenAI, "__init__", lambda *args, **kwargs: pytest.fail("no LLM client allowed"))
    config = {**deepcopy(preflight.DEFAULT_CONFIG), "data_vendors": {"core_stock_apis": "yfinance"}}
    supplied_before = deepcopy(config)
    result = preflight.check_data("NVDA", "2026-10-17", config)
    assert result["core_ready"] and len(result["checks"]) == 16
    assert get_config() == before
    assert defaults_before == preflight.DEFAULT_CONFIG and config == supplied_before
