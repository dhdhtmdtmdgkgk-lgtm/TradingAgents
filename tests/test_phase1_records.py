"""Completed-run persistence, numeric provenance, redaction and observed usage."""

import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from tradingagents import run_records
from tradingagents.stats import StatsCallbackHandler


def completed():
    return {
        "company_of_interest": "NVDA", "trade_date": "2026-01-09", "market_report": "原文 report",
        "final_trade_decision": "**Rating**: Buy", "final_rating": "Buy",
        "investment_debate_state": {"history": "bull/bear"},
        "risk_debate_state": {"history": "risk"}, "investment_plan": "plan", "trader_investment_plan": "proposal",
        "trader_price_levels": {"entry_price": 100, "stop_loss": 90, "source": "structured"},
        "pm_price_adjustments": {"entry_price": None, "stop_loss": 88, "source": "structured"},
    }


def test_multiple_runs_same_date_are_immutable_and_keep_price_provenance(tmp_path):
    data = completed()
    first = run_records.write_completed_run(tmp_path, data, {"deep_think_llm": "model"})
    original = first.read_bytes()
    data["final_rating"] = "Hold"
    second = run_records.write_completed_run(tmp_path, data, {})
    assert first != second and first.read_bytes() == original
    saved = json.loads(first.read_text(encoding="utf-8"))
    assert saved["schema_version"] == 1 and saved["status"] == "completed"
    assert saved["prices"]["trader_original"]["stop_loss"] == 90
    assert saved["prices"]["pm_adjustments"]["entry_price"] is None
    assert saved["prices"]["effective_reference"] == {"entry_price": 100, "stop_loss": 88}
    assert saved["investment_debate_state"]["history"] == "bull/bear"
    assert saved["market_report"] == "原文 report"


def test_concurrent_archives_have_distinct_ids(tmp_path):
    with ThreadPoolExecutor(max_workers=4) as pool:
        paths = list(pool.map(lambda _: run_records.write_completed_run(tmp_path, completed(), {}), range(8)))
    assert len(set(paths)) == 8
    assert len(list((tmp_path / "runs").glob("*.json"))) == 8
    assert not list((tmp_path / "runs").glob("*.tmp"))


def test_duplicate_id_cannot_overwrite_a_completed_record(tmp_path):
    meta = {"run_id": "a" * 32}
    path = run_records.write_completed_run(tmp_path, completed(), {}, metadata=meta)
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        run_records.write_completed_run(tmp_path, completed(), {}, metadata=meta)
    assert path.read_bytes() == original
    assert not list(path.parent.glob("*.tmp"))


@pytest.mark.parametrize("final", [None, "", "   "])
def test_no_final_decision_means_no_completed_json(tmp_path, final):
    data = completed()
    data["final_trade_decision"] = final
    with pytest.raises(ValueError, match="no final decision"):
        run_records.write_completed_run(tmp_path, data, {})
    assert not list(tmp_path.rglob("*.json"))


def test_review_is_a_completed_output_not_an_invented_hold(tmp_path):
    data = completed()
    data.update(final_trade_decision="No clear rating.", final_rating="REVIEW")
    path = run_records.write_completed_run(tmp_path, data, {})
    assert json.loads(path.read_text(encoding="utf-8"))["final_rating"] == "REVIEW"


def test_failed_publication_cleans_temporary_file(tmp_path, monkeypatch):
    monkeypatch.setattr(run_records.os, "link", Mock(side_effect=OSError("disk unavailable")))
    with pytest.raises(OSError):
        run_records.write_completed_run(tmp_path, completed(), {})
    assert not list(tmp_path.rglob("*.json"))
    assert not list(tmp_path.rglob("*.tmp"))


def test_invalid_json_numbers_do_not_create_a_file(tmp_path):
    data = completed()
    data["risk_debate_state"] = {"invalid": float("nan")}
    with pytest.raises(ValueError):
        run_records.write_completed_run(tmp_path, data, {})
    assert not list(tmp_path.rglob("*"))


def test_credentials_echoed_in_reports_are_redacted(tmp_path, monkeypatch):
    monkeypatch.setenv("EXAMPLE_API_KEY", "example-secret-12345")
    data = completed()
    data["news_report"] = ("example-secret-12345 https://user:pass@example.com/api?apikey=otherkey "
                           "Bearer private-token-123")
    text = run_records.write_completed_run(tmp_path, data, {}).read_text(encoding="utf-8")
    for value in ("example-secret-12345", "user:pass", "otherkey", "private-token-123"):
        assert value not in text
    assert "[REDACTED]" in text


def test_missing_or_unlabelled_prices_remain_null(tmp_path):
    data = completed()
    data.pop("trader_price_levels")
    data.pop("pm_price_adjustments")
    data["trader_investment_plan"] = "Maybe buy at 100 and stop at 90."
    prices = json.loads(run_records.write_completed_run(tmp_path, data, {}).read_text(encoding="utf-8"))["prices"]
    assert prices["effective_reference"] == {"entry_price": None, "stop_loss": None}


def test_usage_distinguishes_missing_metadata_and_resets_per_invocation():
    handler = StatsCallbackHandler()
    start = handler.get_stats()
    handler.on_chat_model_start({}, [[]])
    handler.on_llm_end(LLMResult(generations=[[ChatGeneration(message=AIMessage("no usage"))]]))
    assert handler.since(start)["status"] == "unavailable"
    assert handler.since(start)["tokens_in"] is None
    next_start = handler.get_stats()
    handler.on_chat_model_start({}, [[]])
    handler.on_llm_end(LLMResult(generations=[[ChatGeneration(message=AIMessage(
        "with usage", usage_metadata={"input_tokens": 11, "output_tokens": 7, "total_tokens": 18}))]]))
    current = handler.since(next_start)
    assert current["status"] == "complete" and current["llm_calls"] == 1
    assert current["tokens_in"] == 11 and current["tokens_out"] == 7
    assert handler.since(start)["status"] == "partial"


def test_cli_distinguishes_completed_analysis_from_archive_failure(monkeypatch):
    from typer.testing import CliRunner

    from cli import main

    monkeypatch.setattr(main, "run_analysis", Mock(side_effect=run_records.CompletedRunSaveError(
        "Analysis completed, but saving its JSON failed. Check the results directory.")))
    result = CliRunner().invoke(main.app, [])
    assert result.exit_code == 1
    assert "Analysis completed, but saving its JSON failed" in result.output


def test_same_callback_bound_to_model_and_graph_is_not_double_counted():
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    handler = StatsCallbackHandler()
    model = FakeListChatModel(responses=["offline reply"], callbacks=[handler])
    model.invoke("test", config={"callbacks": [handler]})
    assert handler.get_stats()["llm_calls"] == 1
    assert handler.get_stats()["responses"] == 1
