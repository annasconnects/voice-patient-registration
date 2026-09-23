"""FastAPI entry point: wires routers, error handling and startup."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.db import engine, init_db
from app.logging_config import setup_logging
from app.responses import ApiError, fail, ok, pydantic_errors_to_details
from app.routers import calls, patients, vapi
from app.seed import seed_if_empty

setup_logging(settings.log_level)
logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    if settings.seed_data:
        seed_if_empty()
    logger.info("startup complete (db=%s)", engine.url.get_backend_name())
    yield


app = FastAPI(
    title="Voice AI Patient Registration API",
    version="1.0.0",
    description="REST API behind a phone-based patient-registration voice agent.",
    lifespan=lifespan,
)

app.include_router(patients.router)
app.include_router(calls.router)
app.include_router(vapi.router)


# ------------------------------------------------------------------ error envelope


@app.exception_handler(ApiError)
async def _api_error(_: Request, exc: ApiError):
    return fail(exc.status_code, exc.code, exc.message, exc.details)


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, exc: RequestValidationError):
    errors = exc.errors()
    if any(e.get("type") == "json_invalid" for e in errors):
        return fail(400, "bad_request", "request body is not valid JSON")
    if any(e.get("type") == "missing" and tuple(e.get("loc", ())) == ("body",) for e in errors):
        return fail(400, "bad_request", "request body is required")
    return fail(422, "validation_error", "one or more fields are invalid", pydantic_errors_to_details(errors))


@app.exception_handler(StarletteHTTPException)
async def _http_error(_: Request, exc: StarletteHTTPException):
    code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
    return fail(exc.status_code, code, str(exc.detail))


@app.exception_handler(Exception)
async def _unhandled(_: Request, exc: Exception):
    logger.exception("unhandled_error")
    return fail(500, "internal_error", "an unexpected error occurred")


# ------------------------------------------------------------------ misc routes


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/dashboard")


@app.get("/health", tags=["ops"])
def health():
    """Liveness + DB connectivity (used by the host's health check)."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return ok({"status": "ok", "database": "ok"})
    except Exception:
        logger.exception("health_db_failed")
        return fail(503, "db_unavailable", "database is unreachable")


@app.get("/dashboard", include_in_schema=False)
def dashboard():
    return FileResponse(Path(__file__).parent / "static" / "dashboard.html")
