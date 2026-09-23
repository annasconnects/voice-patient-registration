#!/usr/bin/env python3
"""Text-mode simulator: talk to the same agent (same prompt, same tools, same
backend handlers) in your terminal, without a phone. Useful for iterating on
the prompt, and as the fallback demo if telephony is unavailable.

    export OPENAI_API_KEY=...
    python scripts/simulate_call.py            # uses the local DATABASE_URL (SQLite by default)

Type your replies as the caller. Type /quit to hang up.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import date
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db import SessionLocal, init_db  # noqa: E402
from app.services import voice_tools  # noqa: E402
from scripts.setup_vapi import build_assistant  # noqa: E402

CALLER_ID = os.getenv("SIM_CALLER_ID", "+15125550143")


def main() -> None:
    key = os.getenv("OPENAI_API_KEY") or sys.exit("OPENAI_API_KEY is not set")
    init_db()
    assistant = build_assistant("http://local", None)
    prompt = (assistant["model"]["messages"][0]["content"]
              .replace("{{date}}", date.today().isoformat())
              .replace("{{customer.number}}", CALLER_ID))
    tools = [{"type": "function", "function": t["function"]} for t in assistant["model"]["tools"] if t["type"] == "function"]
    tools.append({"type": "function", "function": {"name": "endCall", "description": "Hang up the call.", "parameters": {"type": "object", "properties": {}}}})

    messages = [{"role": "system", "content": prompt}, {"role": "assistant", "content": assistant["firstMessage"]}]
    ctx = voice_tools.CallContext(call_id=f"sim-{uuid.uuid4()}", caller_number=CALLER_ID)
    print(f"\nAva: {assistant['firstMessage']}")

    with httpx.Client(timeout=60, headers={"Authorization": f"Bearer {key}"}) as http, SessionLocal() as session:
        while True:
            user = input("\nYou: ").strip()
            if user in ("/quit", "/exit"):
                break
            messages.append({"role": "user", "content": user})
            while True:  # let the model chain tool calls before speaking
                resp = http.post("https://api.openai.com/v1/chat/completions", json={
                    "model": assistant["model"]["model"], "temperature": 0.3,
                    "messages": messages, "tools": tools,
                })
                resp.raise_for_status()
                msg = resp.json()["choices"][0]["message"]
                messages.append(msg)
                if not msg.get("tool_calls"):
                    print(f"\nAva: {msg.get('content')}")
                    break
                for call in msg["tool_calls"]:
                    name = call["function"]["name"]
                    args = json.loads(call["function"]["arguments"] or "{}")
                    if name == "endCall":
                        print("\n[call ended]")
                        voice_tools.record_call_end(session, ctx.call_id, CALLER_ID, "assistant-ended-call", None, None)
                        return
                    result = voice_tools.TOOL_HANDLERS[name](session, ctx, args)
                    print(f"   [tool {name}({json.dumps(args)}) -> {json.dumps(result)}]")
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result)})


if __name__ == "__main__":
    main()
