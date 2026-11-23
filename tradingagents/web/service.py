"""Read existing run JSONs and bridge the unchanged engine to browser snapshots."""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html import escape
from pathlib import Path

from langchain_core.callbacks import BaseCallbackHandler
from markdown_it import MarkdownIt
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tradingagents.dataflows.date_window import get_current_date
from tradingagents.portfolio import PortfolioContext, Position
from tradingagents.run_records import CompletedRunSaveError, redact, write_completed_run

STAGES = [
    ("Price Data", "가격 데이터 확인", None), ("Memory Log", "Memory", None),
    ("Market Analyst", "Market Analyst", "market_report"),
    ("Sentiment Analyst", "Sentiment Analyst", "sentiment_report"),
    ("News Analyst", "News Analyst", "news_report"),
    ("Fundamentals Analyst", "Fundamentals Analyst", "fundamentals_report"),
    ("Bull Researcher", "Bull · 강세 논거", "investment_debate_state.bull_history"),
    ("Bear Researcher", "Bear · 약세 논거", "investment_debate_state.bear_history"),
    ("Research Manager", "Research Manager", "investment_plan"),
    ("Trader", "Trader", "trader_investment_plan"),
    ("Aggressive Analyst", "Risk · Aggressive", "risk_debate_state.aggressive_history"),
    ("Conservative Analyst", "Risk · Conservative", "risk_debate_state.conservative_history"),
    ("Neutral Analyst", "Risk · Neutral", "risk_debate_state.neutral_history"),
    ("Portfolio Manager", "Portfolio Manager", "final_trade_decision"),
]
REPORTS = {name: (label, key) for name, label, key in STAGES if key}
REPORTS.update({"Research Debate": ("연구 토론 전체", "investment_debate_state.history"),
                "Risk Debate": ("위험 토론 전체", "risk_debate_state.history")})
RUN_ID = re.compile(r"\A[a-f0-9]{32}\Z")
MARKDOWN = MarkdownIt("commonmark", {"html": False}).enable("table")
# Reports remain original Markdown; do not cause external image fetches.
MARKDOWN.renderer.rules["image"] = lambda tokens, idx, *args: escape(tokens[idx].content)


class AnalysisInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    ticker: str = Field(min_length=1, max_length=30)
    trade_date: str
    quantity: float | None = None
    average_price: float | None = Field(default=None, ge=0)
    cash: float | None = Field(default=None, ge=0)
    quick_model: str = Field(min_length=1, max_length=160)
    deep_model: str = Field(min_length=1, max_length=160)
    research_rounds: int = Field(default=1, ge=1, le=10)
    risk_rounds: int = Field(default=1, ge=1, le=10)
    confirm_cost: bool = False

    @field_validator("ticker")
    @classmethod
    def symbol(cls, value):
        value = value.strip().upper()
        if not re.fullmatch(r"[A-Z0-9^][A-Z0-9.^=_/-]*", value) or ".." in value:
            raise ValueError("올바른 티커를 입력하세요.")
        return value

    @field_validator("quick_model", "deep_model")
    @classmethod
    def model_id(cls, value):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", value) or value == "custom":
            raise ValueError("실제 모델 ID를 입력하세요.")
        return value

    @field_validator("trade_date")
    @classmethod
    def analysis_date(cls, value):
        if datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d") != value or value > get_current_date():
            raise ValueError("분석일은 오늘 또는 과거 날짜여야 합니다.")
        return value

    @model_validator(mode="after")
    def approval(self):
        if not self.confirm_cost:
            raise ValueError("분석 실행과 API 비용 발생에 동의해 주세요.")
        return self


class SingleHolding:
    """Web-only adapter: a partial single holding is never an empty full book.

    Implements the engine's existing render/fingerprint/model_dump interface;
    PortfolioContext and the full-portfolio CLI remain unchanged.
    """
    def __init__(self, inputs):
        self.inputs = inputs

    def model_dump(self, **kwargs):
        p = self.inputs
        return {"scope": "analyzed_instrument_only", "currency": "USD", "cash": p.cash,
                "positions": [{"ticker": p.ticker, "quantity": p.quantity, "average_price": p.average_price}],
                "other_holdings_provided": False}

    def fingerprint(self):
        return hashlib.sha256(json.dumps(self.model_dump(), sort_keys=True).encode()).hexdigest()[:12]

    def render(self, ticker):
        p = self.inputs
        if p.quantity is not None:
            book = PortfolioContext(cash=p.cash, currency="USD", positions=[
                Position(ticker=p.ticker, quantity=p.quantity, average_price=p.average_price)])
            text = book.render(ticker)
        else:
            text = f"Portfolio at the analysis date:\n- Current quantity in {ticker}: not provided; do not assume zero."
            if p.average_price is not None:
                text += f"\n- Reported average purchase price: {p.average_price} USD; quantity unknown."
            if p.cash is not None:
                text += f"\n- Cash available: {p.cash} USD"
        return text + "\n- Other holdings were not supplied. This is not a complete portfolio."


def holding(inputs):
    return None if all(v is None for v in (inputs.quantity, inputs.average_price, inputs.cash)) else SingleHolding(inputs)


def value_at(state, key):
    value = state
    for part in key.split("."):
        value = value.get(part, {}) if isinstance(value, dict) else {}
    return value if isinstance(value, str) else ""


def reports(state):
    return {name: {"label": label, "markdown": text, "html": MARKDOWN.render(text)}
            for name, (label, key) in REPORTS.items() if (text := value_at(state, key))}


def public_state(state):
    state = redact(state)
    return {"reports": reports(state), "final_rating": state.get("final_rating"),
            "final_markdown": state.get("final_trade_decision", ""),
            "final_html": MARKDOWN.render(state.get("final_trade_decision") or "")}


class History:
    def __init__(self, results_dir):
        self.directory = Path(results_dir) / "runs"

    def read(self, run_id):
        if not RUN_ID.fullmatch(run_id):
            raise KeyError(run_id)
        path = self.directory / f"{run_id}.json"
        try:
            if path.is_symlink() or path.resolve().parent != self.directory.resolve() or path.stat().st_size > 32_000_000:
                raise KeyError(run_id)
            data = json.loads(path.read_text(encoding="utf-8"))
            if (data.get("schema_version") != 1 or data.get("run_id") != run_id
                    or data.get("status") != "completed" or not isinstance(data.get("final_trade_decision"), str)
                    or not data["final_trade_decision"].strip()
                    or not isinstance(data.get("company_of_interest"), str)
                    or not isinstance(data.get("trade_date"), str)
                    or not isinstance(data.get("completed_at"), str)
                    or not isinstance(data.get("run_settings", {}), dict)):
                raise KeyError(run_id)
            return data
        except (OSError, ValueError, AttributeError) as exc:
            raise KeyError(run_id) from exc

    def listing(self, ticker=""):
        items, skipped = [], 0
        for path in self.directory.glob("*.json"):
            try:
                data = self.read(path.stem)
                symbol = str(data.get("company_of_interest", ""))
                if ticker and ticker.strip().upper() not in symbol.upper():
                    continue
                items.append({"id": path.stem, "ticker": symbol, "trade_date": data.get("trade_date"),
                              "completed_at": data.get("completed_at") or "", "rating": data.get("final_rating")})
            except KeyError:
                skipped += 1
        return {"items": sorted(items, key=lambda i: (i["completed_at"], i["id"]), reverse=True),
                "skipped": skipped}

    def detail(self, run_id):
        data = self.read(run_id)
        settings = data.get("run_settings") or {}
        return redact({"id": run_id, "ticker": data.get("company_of_interest"), "trade_date": data.get("trade_date"),
                       "completed_at": data.get("completed_at"), **public_state(data),
                       "settings": {k: settings.get(k) for k in ("quick_think_llm", "deep_think_llm",
                                    "max_debate_rounds", "max_risk_discuss_rounds", "output_language")},
                       "prices": data.get("prices"), "api_usage": data.get("api_usage")})


@dataclass
class Job:
    id: str
    inputs: AnalysisInput
    status: str = "running"
    state: dict = field(default_factory=dict)
    stages: dict = field(default_factory=lambda: {name: "waiting" for name, _, _ in STAGES})
    error: str | None = None
    archive_id: str | None = None
    lock: threading.RLock = field(default_factory=threading.RLock)

    def snapshot(self):
        with self.lock:
            return {"id": self.id, "ticker": self.inputs.ticker, "trade_date": self.inputs.trade_date,
                    "status": self.status, "stages": dict(self.stages), "error": self.error,
                    "archive_id": self.archive_id, **public_state(self.state)}

    def update(self, chunk):
        with self.lock:
            self.state.update(chunk)
            for name, (_, key) in REPORTS.items():
                if name in self.stages and value_at(chunk, key):
                    self.stages[name] = "completed"
            if "past_context" in chunk:
                self.stages["Memory Log"] = "completed"


class Progress(BaseCallbackHandler):
    def __init__(self, job):
        self.job = job
        self.steps = {}

    def on_chain_start(self, serialized, inputs, *, run_id, metadata=None, **kwargs):
        node = (metadata or {}).get("langgraph_node")
        if node in self.job.stages:
            with self.job.lock:
                self.steps[run_id] = node
                self.job.stages[node] = "running"

    def on_chain_end(self, outputs, *, run_id, **kwargs):
        node = self.steps.pop(run_id, None)
        if node in ("Price Data", "Memory Log"):
            with self.job.lock:
                self.job.stages[node] = "completed"

    def on_chain_error(self, error, *, run_id, **kwargs):
        node = self.steps.pop(run_id, None)
        if node:
            with self.job.lock:
                self.job.stages[node] = "failed"


class BusyError(Exception):
    pass


class Jobs:
    def __init__(self, config, graph_factory=None, demo=False):
        self.config = deepcopy(config)
        self.graph_factory = graph_factory
        self.demo = demo
        self.items = {}
        self.active_id = None
        self.lock = threading.Lock()

    def start(self, inputs):
        with self.lock:
            if self.active_id and self.items[self.active_id].status == "running":
                raise BusyError
            # Keep only a bounded set of terminal job snapshots; archives live on disk.
            while len(self.items) >= 20:
                self.items.pop(next(iter(self.items)))
            job = Job(uuid.uuid4().hex, inputs)
            self.items[job.id] = job
            self.active_id = job.id
            threading.Thread(target=self._run, args=(job,), daemon=True).start()
            return job

    def _run(self, job):
        p = job.inputs
        cfg = {**deepcopy(self.config), "output_language": "Korean", "quick_think_llm": p.quick_model,
               "deep_think_llm": p.deep_model, "max_debate_rounds": p.research_rounds,
               "max_risk_discuss_rounds": p.risk_rounds}
        book = holding(p)
        try:
            if self.demo:
                self._demo(job, cfg, book)
            else:
                factory = self.graph_factory
                if factory is None:
                    from tradingagents.graph.trading_graph import TradingAgentsGraph
                    factory = TradingAgentsGraph
                callback = Progress(job)
                graph = factory(config=cfg, callbacks=[callback])
                # The same streaming/recording lifecycle as the existing CLI.
                with graph.checkpoint_scope(p.ticker, p.trade_date, "stock", book) as thread_id:
                    state = graph.create_run_state(p.ticker, p.trade_date, "stock", book)
                    args = graph.propagator.get_graph_args(callbacks=[callback])
                    if thread_id:
                        args["config"].setdefault("configurable", {})["thread_id"] = thread_id
                    for _, chunk in graph.stream_run(graph.checkpoint_input(state), **args):
                        if chunk:
                            # Never retain tool messages, request metadata or credentials in the browser snapshot.
                            job.update({k: v for k, v in chunk.items() if k not in ("messages",)})
                    path = graph.record_decision(p.ticker, p.trade_date, job.state)
                    job.archive_id = Path(path).stem
                    graph.clear_checkpoint_on_success(p.ticker, p.trade_date, "stock", book)
            with job.lock:
                job.status = "completed"
                for name in job.stages:
                    if job.stages[name] != "failed":
                        job.stages[name] = "completed"
        except Exception as exc:
            with job.lock:
                job.status = "failed"
                # Fixed messages only: exception text may contain credentials/URLs.
                job.error = ("분석은 완료됐지만 결과 JSON 저장에 실패했습니다. 서버의 저장 경로를 확인하세요."
                             if isinstance(exc, CompletedRunSaveError)
                             else "분석이 중단됐습니다. 서버의 API·데이터·저장 경로 설정을 확인하세요. 자동 재실행하지 않습니다.")
                for name, status in job.stages.items():
                    if status == "running":
                        job.stages[name] = "failed"

    def _demo(self, job, cfg, book):
        """Explicit opt-in, offline UI exercise; no graph, Memory or external APIs."""
        p = job.inputs
        job.update({"company_of_interest": p.ticker, "trade_date": p.trade_date, "asset_type": "stock"})
        for name, _, key in STAGES:
            with job.lock:
                job.stages[name] = "running"
            time.sleep(0.7)
            if key and "." not in key:
                job.update({key: f"## {name}\n\n**모의 데이터** · 실제 투자 분석이 아닙니다.\n\n"
                                  f"{p.ticker} 대시보드의 원문 보고서 열람을 확인하는 예제입니다.\n\n"
                                  "| 검증 항목 | 결과 |\n|---|---|\n| 보고서 전달 | 정상 |"})
            if "." in (key or ""):
                group, field_name = key.split(".")
                count = p.research_rounds if group.startswith("investment") else p.risk_rounds
                text = "\n\n".join(f"### {name} · {i+1}라운드\n\n모의 토론 발언입니다." for i in range(count))
                block = dict(job.state.get(group) or {})
                block[field_name] = text
                block["history"] = block.get("history", "") + "\n\n" + text
                job.update({group: block})
            with job.lock:
                job.stages[name] = "completed"
        job.update({"final_rating": "Hold", "portfolio_context": book.render(p.ticker) if book else "",
                    "final_trade_decision": "## 모의 최종 보고서\n\n**Hold · UI 검증 예제**\n\n"
                    "실제 시장 데이터나 LLM을 사용하지 않았습니다.\n\n"
                    "### 판단 근거\n\n분석·기록·토론 모달의 연결을 확인하는 샘플입니다."})
        path = write_completed_run(cfg["results_dir"], job.state,
                                   {"output_language": "Korean", "demo": True,
                                    "quick_think_llm": p.quick_model, "deep_think_llm": p.deep_model,
                                    "max_debate_rounds": p.research_rounds, "max_risk_discuss_rounds": p.risk_rounds},
                                   metadata={"portfolio": book.model_dump() if book else None,
                                             "started_at": datetime.now(UTC).isoformat()},
                                   usage={"status": "complete", "llm_calls": 0, "tokens_in": 0, "tokens_out": 0})
        job.archive_id = path.stem
