"""Optional local/LAN server. Run with tradingagents-web or python -m tradingagents.web.app."""
import argparse
import hmac
import secrets
import socket
from contextlib import suppress
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from tradingagents.dataflows.date_window import get_current_date
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.llm_clients.factory import tier_provider
from tradingagents.llm_clients.model_catalog import get_model_options
from tradingagents.web.service import STAGES, AnalysisInput, BusyError, History, Jobs

STATIC = Path(__file__).with_name("static")


def local_hosts():
    hosts = {"localhost", "127.0.0.1", "::1", socket.gethostname().lower()}
    with suppress(OSError):
        hosts.update(socket.gethostbyname_ex(socket.gethostname())[2])
    return hosts


def create_app(config=None, *, graph_factory=None, demo=False, allowed_hosts=None):
    config = deepcopy(config or DEFAULT_CONFIG)
    jobs, history = Jobs(config, graph_factory, demo), History(config["results_dir"])
    token = secrets.token_urlsafe(32)
    hosts = allowed_hosts or local_hosts()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.jobs = jobs
    app.state.history = history

    @app.middleware("http")
    async def local_access(request: Request, call_next):
        try:
            host = urlsplit("//" + request.headers.get("host", "")).hostname
        except ValueError:
            return JSONResponse({"detail": "잘못된 서버 주소입니다."}, status_code=400)
        if host not in hosts:
            return JSONResponse({"detail": "허용되지 않은 서버 주소입니다."}, status_code=403)
        if request.method not in ("GET", "HEAD"):
            origin = request.headers.get("origin")
            expected = f"{request.url.scheme}://{request.headers['host']}"
            supplied = request.headers.get("x-dashboard-token", "")
            if origin != expected or not supplied.isascii() or not hmac.compare_digest(supplied, token):
                return JSONResponse({"detail": "페이지를 새로고침한 뒤 다시 시도하세요."}, status_code=403)
            try:
                if int(request.headers.get("content-length", "0")) > 16384:
                    return JSONResponse({"detail": "요청이 너무 큽니다."}, status_code=413)
            except ValueError:
                return JSONResponse({"detail": "잘못된 요청입니다."}, status_code=400)
        response = await call_next(request)
        response.headers.update({
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; "
            "frame-src https://*.tradingview.com https://*.tradingview-widget.com; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        })
        return response

    @app.exception_handler(RequestValidationError)
    async def bad_input(request, exc):
        # Do not reflect arbitrary submitted credentials or internal error context.
        return JSONResponse({"detail": "입력값·날짜·모델·숫자 범위와 실행 동의를 확인하세요."}, status_code=422)

    @app.get("/api/config")
    def configuration():
        options = {}
        for tier in ("quick", "deep"):
            provider = tier_provider(config, tier)
            try:
                models = [model for _, model in get_model_options(provider, tier) if model != "custom"]
            except KeyError:
                models = []
            default = config.get(f"{tier}_think_llm") or ("gpt-6-luna" if tier == "quick" else "gpt-6-sol")
            options[tier] = {"provider": provider, "default": default,
                             "models": list(dict.fromkeys([default, *models]))}
        return {"today": get_current_date(), "models": options, "output_language": "Korean",
                "research_rounds": config.get("max_debate_rounds", 1),
                "risk_rounds": config.get("max_risk_discuss_rounds", 1),
                "stages": [{"id": name, "label": label} for name, label, _ in STAGES],
                "demo": demo, "csrf_token": token}

    @app.get("/api/jobs/active")
    def active():
        with jobs.lock:
            job = jobs.items.get(jobs.active_id)
            return job.snapshot() if job else None

    @app.post("/api/jobs", status_code=202)
    def start(inputs: AnalysisInput):
        try:
            return {"id": jobs.start(inputs).id}
        except BusyError as exc:
            raise HTTPException(409, "이미 분석이 진행 중입니다.") from exc

    @app.get("/api/jobs/{job_id}")
    def progress(job_id: str):
        with jobs.lock:
            job = jobs.items.get(job_id)
        if job is None:
            raise HTTPException(404, "실행 상태가 없습니다. 완료 기록을 확인하세요.")
        return job.snapshot()

    @app.get("/api/history")
    def records(ticker: str = ""):
        return history.listing(ticker[:30])

    @app.get("/api/history/{run_id}")
    def record(run_id: str):
        try:
            return history.detail(run_id)
        except KeyError as exc:
            raise HTTPException(404, "정상 완료 기록을 찾을 수 없습니다.") from exc

    @app.get("/")
    @app.get("/history")
    def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def main():
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="Use 0.0.0.0 for a trusted LAN")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--results-dir", help="Read/write existing UUID run archives here")
    parser.add_argument("--allow-host", action="append", default=[], help="Additional local IP/hostname")
    parser.add_argument("--demo", action="store_true", help="Offline simulated runs; separate result directory")
    args = parser.parse_args()
    cfg = deepcopy(DEFAULT_CONFIG)
    if args.results_dir:
        cfg["results_dir"] = str(Path(args.results_dir).resolve())
    elif args.demo:
        cfg["results_dir"] = str(Path.home() / ".tradingagents" / "dashboard-demo")
    hosts = local_hosts() | set(args.allow_host)
    if args.host not in ("0.0.0.0", "::"):
        hosts.add(args.host)
    print(f"TradingAgents {'DEMO ' if args.demo else ''}http://127.0.0.1:{args.port}")
    if args.host in ("0.0.0.0", "::"):
        print("LAN addresses: " + ", ".join(f"http://{h}:{args.port}" for h in sorted(hosts)
                                             if h not in ("::1", "localhost")))
    uvicorn.run(create_app(cfg, demo=args.demo, allowed_hosts=hosts), host=args.host, port=args.port,
                log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
