"""Price provenance and the PM's narrowly scoped supporting market context."""

from unittest.mock import Mock

import pytest
from langchain_core.messages import AIMessage

from tradingagents.agents.managers.portfolio_manager import create_portfolio_manager
from tradingagents.agents.prices import price_levels
from tradingagents.agents.schemas import (
    PortfolioDecision,
    PortfolioRating,
    TraderAction,
    TraderProposal,
)
from tradingagents.agents.trader.trader import create_trader


def state():
    return {
        "company_of_interest": "NVDA", "market_report": "price=100, ATR=4\n" + "evidence " * 2000,
        "investment_plan": "Research plan", "trader_investment_plan": "Original Trader proposal",
        "trader_price_levels": {"entry_price": 100.0, "stop_loss": 90.0},
        "risk_debate_state": {"history": "Risk evidence", "aggressive_history": "a", "conservative_history": "c",
                              "neutral_history": "n", "count": 3, "current_aggressive_response": "a",
                              "current_conservative_response": "c", "current_neutral_response": "n"},
    }


def llm_for(decision):
    llm = Mock()
    llm.with_structured_output.return_value.invoke.return_value = decision
    return llm


def decision(**fields):
    return PortfolioDecision(rating=PortfolioRating.OVERWEIGHT, executive_summary="s", investment_thesis="t", **fields)


def test_trader_keeps_original_numeric_fields_without_extra_model_call():
    proposal = TraderProposal(action=TraderAction.BUY, reasoning="r", entry_price=100, stop_loss=90)
    llm = llm_for(proposal)
    result = create_trader(llm)(state())
    assert result["trader_price_levels"] == {"entry_price": 100, "stop_loss": 90, "source": "structured"}
    llm.with_structured_output.return_value.invoke.assert_called_once()
    llm.invoke.assert_not_called()


@pytest.mark.parametrize("entry,stop,expected", [
    (None, None, (None, None)), (100, 90, (None, None)), (98, None, (98, None)),
    (None, 88, (None, 88)), ("15%", "N/A", (None, None)),
    (float("nan"), float("inf"), (None, None)),
])
def test_pm_corrections_are_optional_and_unchanged_values_are_null(entry, stop, expected):
    llm = llm_for(decision(entry_price=entry, stop_loss=stop))
    result = create_portfolio_manager(llm)(state())
    changes = result["pm_price_adjustments"]
    assert (changes["entry_price"], changes["stop_loss"]) == expected
    assert result["final_rating"] == "Overweight"
    assert "**Investment Thesis**: t" in result["final_trade_decision"]


@pytest.mark.parametrize("structured", [True, False])
def test_pm_receives_full_market_report_only_for_price_review(structured):
    llm = llm_for(decision())
    if not structured:
        llm.with_structured_output.side_effect = NotImplementedError
        llm.invoke.return_value = AIMessage("**Rating**: Hold\n**Entry Price Adjustment**: null")
    data = state()
    create_portfolio_manager(llm)(data)
    prompt = (llm.with_structured_output.return_value.invoke if structured else llm.invoke).call_args.args[0]
    assert prompt.count(data["market_report"].strip()) == 1
    for text in ("Research plan", "Original Trader proposal", "Risk evidence", "Preserve reasonable existing prices",
                 "Do not use this report to reopen", "existing\nResearch Manager, Trader and Risk Debate"):
        assert text in prompt


def test_no_market_report_does_not_invent_corrections():
    data = state()
    data["market_report"] = ""
    result = create_portfolio_manager(llm_for(decision(entry_price=105, stop_loss=95)))(data)
    assert result["pm_price_adjustments"]["entry_price"] is None
    assert result["pm_price_adjustments"]["stop_loss"] is None


def test_fallback_prices_accept_only_explicit_unique_labels():
    original = price_levels(text="Entry at 100. Stop at 90.\n**Entry Price**: 101\n**Stop Loss**: 15%")
    assert original["entry_price"] == 101
    assert original["stop_loss"] is None
    changes = price_levels(text="**Entry Price**: 100\n**Entry Price Adjustment**: 98\n"
                          "**Stop Loss Adjustment**: 88\n**Stop Loss Adjustment**: 87", adjustments=True)
    assert changes["entry_price"] == 98
    assert changes["stop_loss"] is None


def test_pm_fallback_keeps_correction_separate_from_original():
    llm = Mock()
    llm.with_structured_output.side_effect = NotImplementedError
    llm.invoke.return_value = AIMessage("**Rating**: Buy\n**Entry Price**: 100\n"
                                      "**Entry Price Adjustment**: 98\n**Stop Loss Adjustment**: null")
    data = state()
    result = create_portfolio_manager(llm)(data)
    assert data["trader_price_levels"]["entry_price"] == 100
    assert result["pm_price_adjustments"]["entry_price"] == 98
    assert result["pm_price_adjustments"]["stop_loss"] is None
