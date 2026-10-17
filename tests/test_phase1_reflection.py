"""The reflector may report insufficient evidence; it does not have to invent a lesson."""

from unittest.mock import Mock

from langchain_core.messages import AIMessage

from tradingagents.memory.reflection import Reflector


def test_reflector_receives_only_supplied_evidence_and_can_abstain():
    llm = Mock()
    llm.invoke.return_value = AIMessage("No reliable lesson can be drawn from the available evidence.")
    result = Reflector(llm).reflect_on_final_decision("Sell: risks may persist for months.", .03, .01,
                                                  benchmark_name="SPY", holding_days=5)
    system, human = llm.invoke.call_args.args[0]
    prompt = system[1]
    assert "no reliable lesson can be drawn" in prompt
    assert "Price movement alone does not establish causation" in prompt
    assert "do not invent news" in prompt
    assert "not executed strategy profit" in prompt
    assert "too short" in prompt
    assert "One concrete lesson to apply" not in prompt
    assert "+3.0%" in human[1] and "+1.0%" in human[1]
    assert "Alpha vs SPY" in human[1] and "5 trading days" in human[1]
    assert "Sell: risks may persist for months." in human[1]
    assert result == llm.invoke.return_value.content
    llm.invoke.assert_called_once()
