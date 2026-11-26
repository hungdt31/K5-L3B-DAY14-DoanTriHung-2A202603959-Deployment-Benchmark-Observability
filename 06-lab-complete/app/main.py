"""
Production AI Agent — Part 6 Final Project.

Chạy:   uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
Kiểm:   python check_production_ready.py

  GET  /health  liveness  — luôn 200 khi process còn sống (kể cả Redis chết)
  GET  /ready   readiness — 200 khi Redis OK; 503 khi Redis mất kết nối hoặc đang tắt
  POST /ask     X-API-Key → rate limit (Redis) → cost guard (Redis) → LLM
"""
import json
import logging
import signal
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import redis
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import cost_guard
from app.auth import verify_api_key
from app.config import settings
from app.rate_limiter import check_rate_limit
from app.redis_client import redis_healthy
from utils.mock_llm import ask as llm_ask

APP_VERSION = "1.0.0"
EXPECTED_OUTPUT_TOKENS = 150      # giữ chỗ budget cho phần trả lời trước khi biết độ dài thật


# ─────────────────────────────────────────────────────────
# Structured logging — mỗi dòng log là 1 JSON object
# ─────────────────────────────────────────────────────────
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "lvl": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        entry.update(getattr(record, "fields", {}))
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)   # traceback nằm gọn trong 1 dòng JSON
        return json.dumps(entry, ensure_ascii=False, default=str)


def _setup_logging() -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    log = logging.getLogger("agent")
    log.handlers[:] = [handler]
    log.setLevel(settings.log_level)
    log.propagate = False       # không đi qua root logger → không bị format lại thành text
    return log


logger = _setup_logging()


def log_event(event: str, level: int = logging.INFO, **fields) -> None:
    logger.log(level, event, extra={"fields": fields})


# ─────────────────────────────────────────────────────────
# Lifecycle + graceful shutdown
# ─────────────────────────────────────────────────────────
class _State:
    shutting_down = False


state = _State()


def _hook_sigterm() -> None:
    """
    Khi nhận SIGTERM: đánh dấu shutting_down để /ready trả 503 ngay (load balancer rút instance ra),
    rồi GỌI LẠI handler của uvicorn để uvicorn ngừng nhận kết nối mới, chờ request đang chạy xong
    và chạy phần shutdown của lifespan. Không gọi lại thì process sẽ không bao giờ tắt.
    """
    previous = signal.getsignal(signal.SIGTERM)

    def on_sigterm(signum, frame):
        state.shutting_down = True
        log_event("sigterm_received", signal=signum)
        if callable(previous):
            previous(signum, frame)

    try:
        signal.signal(signal.SIGTERM, on_sigterm)
    except ValueError:          # không ở main thread (vd chạy trong test client) → bỏ qua
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    _hook_sigterm()
    log_event("startup", version=APP_VERSION, environment=settings.environment,
              rate_limit_per_minute=settings.rate_limit_per_minute,
              daily_budget_usd=settings.daily_budget_usd,
              redis_ok=redis_healthy(), api_key_configured=bool(settings.agent_api_key))
    if not settings.agent_api_key:
        log_event("agent_api_key_missing", logging.WARNING,
                  note="development mode: mọi request /ask sẽ bị 401")
    yield
    # Tới đây uvicorn đã ngừng nhận request mới và đã chờ các request đang chạy hoàn thành.
    state.shutting_down = True
    try:
        from app.redis_client import r
        r.close()
    except Exception:
        pass
    log_event("shutdown_complete")


app = FastAPI(title="Production AI Agent", version=APP_VERSION, lifespan=lifespan,
              docs_url=None if settings.environment == "production" else "/docs",
              redoc_url=None)


# ─────────────────────────────────────────────────────────
# Middleware: request id + log JSON mỗi request
# ─────────────────────────────────────────────────────────
@app.middleware("http")
async def access_log(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    started = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        if request.url.path != "/health":            # health check gọi liên tục → không làm nhiễu log
            log_event("request", request_id=request_id, method=request.method,
                      path=request.url.path, status=status,
                      ms=round((time.perf_counter() - started) * 1000, 1))


# ─────────────────────────────────────────────────────────
# Error handling: mã lỗi rõ ràng, body JSON gọn, KHÔNG lộ stack trace
# ─────────────────────────────────────────────────────────
@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail},
                        headers=getattr(exc, "headers", None))


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    errors = [{"field": "body" if e["type"] == "json_invalid"
               else ".".join(str(p) for p in e["loc"][1:]) or "body",
               "msg": e["msg"]}
              for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": "Invalid request body", "errors": errors})


@app.exception_handler(redis.RedisError)
async def redis_error(request: Request, exc: redis.RedisError):
    # Không kiểm tra được rate limit / budget → từ chối (fail closed) thay vì gọi LLM không kiểm soát
    log_event("redis_unavailable", logging.ERROR, path=request.url.path, error=type(exc).__name__)
    return JSONResponse(status_code=503, content={"detail": "Service temporarily unavailable"},
                        headers={"Retry-After": "5"})


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    # Traceback chỉ vào log (1 dòng JSON, field "exc"), client chỉ nhận thông báo chung
    logger.error("unhandled_error", exc_info=exc,
                 extra={"fields": {"path": request.url.path, "error": type(exc).__name__}})
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# ─────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────
class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    user_id: str | None = Field(default=None, max_length=128)


@app.get("/")
def root():
    return {"app": "Production AI Agent", "version": APP_VERSION, "docs": "/docs"}


@app.get("/health")
def health():
    """Liveness: chỉ trả lời 'process còn sống'. Không kiểm tra Redis — Redis chết thì restart
    agent cũng không giúp gì, còn có thể làm platform restart hàng loạt instance."""
    return {"status": "ok", "version": APP_VERSION, "environment": settings.environment}


@app.get("/ready")
def ready():
    """Readiness: có nên nhận traffic không? 503 khi Redis mất kết nối hoặc đang tắt."""
    if state.shutting_down:
        return JSONResponse(status_code=503, content={"ready": False, "reason": "shutting_down"})
    if not redis_healthy():
        return JSONResponse(status_code=503, content={"ready": False, "reason": "redis_unavailable"})
    return {"ready": True}


@app.post("/ask")
def ask(body: AskRequest, api_key: str = Depends(verify_api_key)):
    # `def` thường (không async): FastAPI chạy trong threadpool, nên lời gọi LLM blocking
    # không chặn event loop — /health vẫn trả lời ngay khi đang có request chậm.
    remaining = check_rate_limit(api_key)

    input_tokens = cost_guard.estimate_tokens(body.question)
    reserved = cost_guard.cost_usd(input_tokens, EXPECTED_OUTPUT_TOKENS)
    cost_guard.check_budget(reserved)

    try:
        answer = llm_ask(body.question)
    except Exception:
        cost_guard.release(reserved)
        raise
    output_tokens = cost_guard.estimate_tokens(answer)
    cost = cost_guard.record_cost(input_tokens, output_tokens, reserved)

    log_event("llm_call", user_id=body.user_id, question_chars=len(body.question),
              input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=round(cost, 8))
    return {
        "question": body.question,
        "answer": answer,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                  "cost_usd": round(cost, 8), "requests_remaining": remaining},
    }
