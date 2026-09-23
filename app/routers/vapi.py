"""Telephony boundary: the single webhook Vapi calls.

Vapi owns the phone number, speech-to-text, the LLM turn loop and text-to-speech.
It POSTs here when the LLM invokes a tool (`tool-calls`) and when a call ends
(`end-of-call-report`). This module only translates Vapi's wire format into
calls on `services.voice_tools`; no business logic lives here."""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_session
from app.logging_config import log_event
from app.services import voice_tools
from app.services.voice_tools import CallContext

router = APIRouter(prefix="/vapi", tags=["voice-agent"])
logger = logging.getLogger("vapi")


def _extract_tool_call(item: dict) -> tuple[str | None, str | None, dict]:
    """Vapi has shipped a few shapes over time; accept all of them:
    {id, name, arguments|parameters} or {id, function: {name, arguments}}."""
    call_id = item.get("id") or (item.get("toolCall") or {}).get("id")
    fn = item.get("function") or {}
    name = item.get("name") or fn.get("name")
    args: Any = (
        fn.get("arguments")
        or item.get("arguments")
        or item.get("parameters")
        or (item.get("toolCall") or {}).get("parameters")
        or {}
    )
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except json.JSONDecodeError:
            args = {}
    return call_id, name, args if isinstance(args, dict) else {}


def _context(message: dict) -> CallContext:
    call = message.get("call") or {}
    customer = message.get("customer") or call.get("customer") or {}
    return CallContext(call_id=call.get("id"), caller_number=customer.get("number"))


@router.post("/webhook")
async def vapi_webhook(
    request: Request,
    session: Session = Depends(get_session),
    x_vapi_secret: str | None = Header(default=None),
):
    if settings.vapi_secret and x_vapi_secret != settings.vapi_secret:
        log_event(logger, "webhook_unauthorized", None, logging.WARNING)
        return JSONResponse(status_code=401, content={"error": "unauthorized"})

    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse(status_code=400, content={"error": "invalid JSON"})
    message = body.get("message") or {}
    msg_type = message.get("type")
    ctx = _context(message)

    if msg_type == "tool-calls":
        items = message.get("toolCallList") or [
            {"id": (t.get("toolCall") or {}).get("id"), "name": t.get("name"), **(t.get("toolCall") or {})}
            for t in message.get("toolWithToolCallList") or []
        ]
        results = []
        for item in items:
            tool_call_id, name, args = _extract_tool_call(item)
            handler = voice_tools.TOOL_HANDLERS.get(name or "")
            log_event(logger, "tool_call", {"call_id": ctx.call_id, "tool": name, "args": args})
            if handler is None:
                result: dict = {"status": "error", "message": f"unknown tool {name}"}
            else:
                try:
                    result = handler(session, ctx, args)
                except Exception:  # never leave the caller in silence
                    session.rollback()
                    logger.exception("tool_handler_crashed")
                    result = {
                        "status": "error",
                        "next_step": "Apologize for a technical problem on our end and offer to try again.",
                    }
            log_event(logger, "tool_result", {"call_id": ctx.call_id, "tool": name, "result": result})
            results.append({"toolCallId": tool_call_id, "name": name, "result": json.dumps(result)})
        return {"results": results}

    if msg_type == "end-of-call-report":
        artifact = message.get("artifact") or {}
        analysis = message.get("analysis") or {}
        transcript = artifact.get("transcript") or message.get("transcript")
        summary = analysis.get("summary") or message.get("summary")
        if ctx.call_id:
            try:
                voice_tools.record_call_end(
                    session, ctx.call_id, ctx.caller_number, message.get("endedReason"), transcript, summary
                )
            except Exception:
                session.rollback()
                logger.exception("record_call_end_failed")
        return {"ok": True}

    # status-update, speech-update, etc.: acknowledge and ignore.
    log_event(logger, "vapi_event", {"type": msg_type, "call_id": ctx.call_id}, logging.DEBUG)
    return {"ok": True}
