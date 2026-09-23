"""Consistent JSON envelope: {"data": ..., "error": null | {code, message, details}}."""
from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse


def ok(data: Any, status_code: int = 200) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"data": data, "error": None})


def fail(status_code: int, code: str, message: str, details: Any = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"data": None, "error": {"code": code, "message": message, "details": details}},
    )


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


def pydantic_errors_to_details(errors: list[dict]) -> list[dict]:
    """Flatten Pydantic errors into [{field, message}] with readable messages."""
    details = []
    for err in errors:
        loc = [str(p) for p in err.get("loc", []) if p not in ("body", "query", "path")]
        msg = err.get("msg", "invalid value")
        msg = msg.removeprefix("Value error, ")
        details.append({"field": ".".join(loc) or None, "message": msg})
    return details
