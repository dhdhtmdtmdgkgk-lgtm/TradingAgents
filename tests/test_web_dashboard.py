"""Web/engine bridge tests with isolated JSONs and a streaming fake graph; no paid calls."""
import json
import threading
import time
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.run_records import write_completed_run
from tradingagents.web.app import create_app
from tradingagents.web.service import AnalysisInput, History, Jobs, Progress, holding, public_state


def inputs(**changes):
    return AnalysisInput.model_validate({"ticker": "NVDA", "trade_date": "2026-09-01",
                                        "quick_model": "gpt-6-luna", "deep_model": "gpt-6-sol",
                                        "confirm_cost": True, **changes})


def config(tmp_path):
    return {**deepcopy(DEFAULT_CONFIG), "results_dir": str(tmp_path),
            "data_cache_dir": str(tmp_path / "cache"), "memory_log_path": str(tmp_path / "memory.md")}


def wait_for(job):
    deadline = time.monotonic() + 5
    while job.status == "running" and time.monotonic() < deadline:
        time.sleep(.01)
    assert job.status != "running"


@pytest.mark.unit
def test_partial_single_holding_distinguishes_unknown_zero_and_other_holdings():
    assert holding(inputs()) is None
    unknown = holding(inputs(cash=0, average_price=0))
    assert unknown.model_dump()["positions"][0]["quantity"] is None
    assert "do not assume zero" in unknown.render("NVDA")
    assert "Cash available: 0" in unknown.render("NVDA")
    zero = holding(inputs(quantity=0))
    assert zero.model_dump()["positions"][0]["quantity"] == 0
    assert "0 units" in zero.render("NVDA")
    assert "not a complete portfolio" in zero.render("NVDA")
    assert zero.fingerprint() != unknown.fingerprint()


@pytest.mark.unit
@pytest.mark.parametrize("change", [{"cash": float("nan")}, {"average_price": -1},
                                    {"research_rounds": 0}, {"confirm_cost": False},
                                    {"backend_url": "https://evil.invalid"}, {"ticker": "../../env"}])
def test_input_validation_rejects_invalid_or_internal_settings(change):
    with pytest.raises(ValueError):
        inputs(**change)


class FakeGraph:
    def __init__(self, config, callbacks):
        self.config, self.callback = config, callbacks[0]
        self.propagator = SimpleNamespace(get_graph_args=lambda callbacks: {"config": {"callbacks": callbacks}})
        self.created = None
        self.saved = False
        self.closed = False

    @contextmanager
    def checkpoint_scope(self, ticker, date, asset, book):
        try:
            yield "checkpoint-id"
        finally:
            self.closed = True

    def create_run_state(self, ticker, date, asset, book):
        self.created = ticker, date, asset, book
        return {"company_of_interest": ticker, "trade_date": date, "asset_type": asset}

    def checkpoint_input(self, state):
        return state

    def stream_run(self, state, **kwargs):
        assert kwargs["config"]["configurable"]["thread_id"] == "checkpoint-id"
        yield [], state
        for name, key in [("Market Analyst", "market_report"), ("Fundamentals Analyst", "fundamentals_report"),
                          ("News Analyst", "news_report"), ("Sentiment Analyst", "sentiment_report")]:
            self.callback.on_chain_start({}, {}, run_id=name, metadata={"langgraph_node": name})
            yield [], {key: f"# {name}\n\n원문 **그대로**"}
        for i in range(self.config["max_debate_rounds"]):
            yield [], {"investment_debate_state": {"bull_history": "bull " * (i+1),
                       "bear_history": "bear " * (i+1), "history": "debate " * (i+1), "count": 2*(i+1)}}
        yield [], {"investment_plan": "계획", "trader_investment_plan": "트레이더"}
        yield [], {"risk_debate_state": {"aggressive_history": "공격", "conservative_history": "보수",
                   "neutral_history": "중립", "history": "위험 토론", "count": 3}}
        self.on_partial()
        yield [], {"final_trade_decision": "# PM 원문\n\n순서와 **내용** 유지", "final_rating": "Hold"}

    def on_partial(self):
        pass

    def record_decision(self, ticker, date, state):
        self.saved = True
        return write_completed_run(self.config["results_dir"], state, {"output_language": self.config["output_language"]})

    def clear_checkpoint_on_success(self, *args):
        assert self.saved


@pytest.mark.unit
def test_worker_reuses_stream_and_writer_exposes_partial_and_full_debates(tmp_path):
    made, snapshots = [], []
    manager = Jobs(config(tmp_path), graph_factory=lambda **kw: make(**kw))

    def make(**kw):
        graph = FakeGraph(**kw)
        graph.on_partial = lambda: snapshots.append(next(iter(manager.items.values())).snapshot())
        made.append(graph)
        return graph

    job = manager.start(inputs(research_rounds=2, cash=0))
    wait_for(job)
    assert job.status == "completed" and made[0].closed
    assert made[0].config["output_language"] == "Korean"
    assert made[0].config["max_debate_rounds"] == 2
    assert snapshots[0]["status"] == "running"
    assert snapshots[0]["reports"]["Market Analyst"]["markdown"].endswith("원문 **그대로**")
    assert snapshots[0]["reports"]["Research Debate"]["markdown"] == "debate debate "
    assert snapshots[0]["final_markdown"] == ""
    detail = History(tmp_path).detail(job.archive_id)
    assert detail["final_markdown"] == job.state["final_trade_decision"]
    assert "<strong>내용</strong>" in detail["final_html"]
    assert len(list((tmp_path / "runs").glob("*.json"))) == 1


@pytest.mark.unit
def test_failure_does_not_retry_or_write_partial_json_and_keeps_finished_report(tmp_path):
    starts = []

    class Failed(FakeGraph):
        def on_partial(self):
            raise RuntimeError("secret-key-and-private-endpoint")

    def make(**kw):
        starts.append(True)
        return Failed(**kw)

    manager = Jobs(config(tmp_path), graph_factory=make)
    job = manager.start(inputs())
    wait_for(job)
    assert job.status == "failed" and len(starts) == 1
    assert "secret-key" not in json.dumps(job.snapshot())
    assert "Market Analyst" in job.snapshot()["reports"]
    assert not list(tmp_path.rglob("*.json"))


@pytest.mark.unit
def test_single_active_job_rejected_without_starting_another(tmp_path):
    from tradingagents.web.service import BusyError

    gate = threading.Event()

    class Waiting(FakeGraph):
        def on_partial(self):
            gate.wait(3)

    manager = Jobs(config(tmp_path), graph_factory=Waiting)
    job = manager.start(inputs())
    try:
        with pytest.raises(BusyError):
            manager.start(inputs())
    finally:
        gate.set()
    wait_for(job)


@pytest.mark.unit
def test_history_uuid_preserved_search_sort_bad_files_and_no_path_access(tmp_path):
    for symbol, stamp in [("NVDA", "2026-01-01"), ("NVDA", "2026-01-02"), ("AAPL", "2026-01-03")]:
        path = write_completed_run(tmp_path, {"company_of_interest": symbol, "trade_date": "2026-01-01",
                                   "final_trade_decision": "# unchanged"}, {})
        data = json.loads(path.read_text(encoding="utf-8"))
        data["completed_at"] = stamp
        path.write_text(json.dumps(data), encoding="utf-8")
    (tmp_path / "runs" / "broken.json").write_text("bad", encoding="utf-8")
    corrupt_id = "a" * 32
    (tmp_path / "runs" / f"{corrupt_id}.json").write_text(json.dumps({
        "schema_version": 1, "status": "completed", "run_id": corrupt_id,
        "final_trade_decision": "text", "completed_at": {"invalid": "timestamp"}}), encoding="utf-8")
    history = History(tmp_path)
    assert [i["ticker"] for i in history.listing()["items"]] == ["AAPL", "NVDA", "NVDA"]
    assert len(history.listing("nvda")["items"]) == 2
    assert history.listing()["skipped"] == 2
    with pytest.raises(KeyError):
        history.detail("../../.env")


@pytest.mark.unit
def test_markdown_is_original_but_html_scripts_images_and_javascript_links_are_inert():
    text = '# 제목\n\n<script>alert(1)</script>\n\n[x](javascript:alert(1))\n\n![alt](https://tracker.invalid/x)'
    result = public_state({"final_trade_decision": text})
    assert result["final_markdown"] == text
    assert "<script>" not in result["final_html"]
    assert 'href="javascript:' not in result["final_html"]
    assert "<img" not in result["final_html"]


@pytest.mark.unit
def test_http_endpoints_csrf_host_static_history_and_config_secret_allowlist(tmp_path, monkeypatch):
    import sys
    if sys.platform == "win32":
        # asyncio needs a local self-pipe on Windows. Permit only this explicit
        # loopback socket pair; leave the suite's external-network guard intact.
        import _socket
        import socket

        def local_socketpair():
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            client_socket = socket.socket()
            try:
                _socket.socket.connect(client_socket, listener.getsockname())
                server_socket, _ = listener.accept()
                return server_socket, client_socket
            finally:
                listener.close()

        monkeypatch.setattr(socket, "socketpair", local_socketpair)
    monkeypatch.setenv("OPENAI_API_KEY", "private-secret-key")
    cfg = {**config(tmp_path), "backend_url": "https://secret-user:password@private.invalid"}
    with TestClient(create_app(cfg, graph_factory=FakeGraph, allowed_hosts={"testserver"})) as client:
        settings = client.get("/api/config")
        assert settings.status_code == 200 and "private" not in settings.text
        assert client.get("/").status_code == client.get("/history").status_code == 200
        assert client.get("/.env").status_code == 404
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/api/config", headers={"host": "evil.invalid"}).status_code == 403
        payload = inputs().model_dump()
        assert client.post("/api/jobs", json=payload).status_code == 403
        headers = {"Origin": "http://testserver", "X-Dashboard-Token": settings.json()["csrf_token"]}
        response = client.post("/api/jobs", json=payload, headers=headers)
        assert response.status_code == 202
        job = client.app.state.jobs.items[response.json()["id"]]
        wait_for(job)
        assert client.get("/api/jobs/active").json()["status"] == "completed"
        record = client.get("/api/history").json()["items"][0]
        assert client.get("/api/history/" + record["id"]).json()["final_markdown"] == job.state["final_trade_decision"]
        bad = client.post("/api/jobs", json={**payload, "backend_url": "private-secret-key"}, headers=headers)
        assert bad.status_code == 422 and "private-secret-key" not in bad.text


@pytest.mark.unit
def test_progress_callback_marks_failed_stage(tmp_path):
    from tradingagents.web.service import Job
    job = Job("x", inputs())
    callback = Progress(job)
    callback.on_chain_start({}, {}, run_id="one", metadata={"langgraph_node": "News Analyst"})
    assert job.stages["News Analyst"] == "running"
    callback.on_chain_error(RuntimeError(), run_id="one")
    assert job.stages["News Analyst"] == "failed"
