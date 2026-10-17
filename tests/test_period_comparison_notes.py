"""Focused checks for period interpretation notes; all model/data calls mocked."""
import pytest
from langchain_core.messages import AIMessage, HumanMessage

from tradingagents.agents.analysts import fundamentals_analyst
from tradingagents.dataflows.vendors import fred


@pytest.mark.unit
def test_fundamentals_actual_prompt_contains_period_comparison_rule(monkeypatch):
    captured = []

    def take_turn(prompt, llm, tools, messages):
        captured.extend(prompt.format_messages(messages=messages))
        return AIMessage(content="report"), "report"

    monkeypatch.setattr(fundamentals_analyst, "take_turn", take_turn)
    node = fundamentals_analyst.create_fundamentals_analyst(object())
    result = node({"trade_date": "2026-10-17", "company_of_interest": "NVDA",
                   "instrument_context": "NVDA", "messages": [HumanMessage(content="NVDA")]})
    text = captured[0].content
    assert "fiscal-year, single-quarter, year-to-date, TTM and Forward" in text
    assert "EPS alone do not establish a data inconsistency" in text
    assert "claim an inconsistency only with specific like-for-like values" in text
    assert result["fundamentals_report"] == "report"


@pytest.mark.unit
@pytest.mark.parametrize("indicator,needs_note", [
    ("cpi", True), ("CPIAUCSL", True), ("core_cpi", True), ("unemployment", False),
])
def test_cpi_note_does_not_change_values_or_non_cpi_output(monkeypatch, indicator, needs_note):
    def request(path, params):
        if path == "series":
            return {"seriess": [{"title": "Series", "units": "Index", "frequency": "Monthly"}]}
        return {"observations": [{"date": "2025-11-01", "value": "325.063"},
                                 {"date": "2026-09-01", "value": "336.298"}]}

    monkeypatch.setattr(fred, "_request", request)
    text = fred.get_macro_data(indicator, "2026-10-17")
    assert "**Latest:** 336.298 (2026-09-01)" in text
    assert "**Change from 2025-11-01 to 2026-09-01:** +11.24 (+3.46%)" in text
    assert "| 2025-11-01 | 325.063 |" in text and "| 2026-09-01 | 336.298 |" in text
    assert ("not necessarily year-over-year (YoY) inflation" in text) == needs_note
