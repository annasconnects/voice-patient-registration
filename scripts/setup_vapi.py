#!/usr/bin/env python3
"""Create (or update) the Vapi assistant and attach it to your phone number.

The assistant is defined in code (this file + agent/system_prompt.md +
agent/tools.json), so it is versioned alongside the backend instead of living
only in a dashboard.

Usage:
    export VAPI_API_KEY=...            # Vapi dashboard -> API Keys (private key)
    export PUBLIC_BASE_URL=https://your-app.up.railway.app
    export VAPI_SECRET=...             # same value as the backend's VAPI_SECRET
    python scripts/setup_vapi.py                 # create/update assistant, attach to number
    python scripts/setup_vapi.py --dry-run       # just write agent/vapi_assistant.json
    python scripts/setup_vapi.py --buy-number 512   # also try to get a free Vapi number in area code 512

Optional env: VAPI_PHONE_NUMBER_ID (which number to attach), LLM_MODEL (default gpt-4o),
VOICE_ID (ElevenLabs voice id).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.vapi.ai"
ASSISTANT_NAME = "Maple Grove Patient Intake (Ava)"


def load_prompt() -> str:
    raw = (ROOT / "agent" / "system_prompt.md").read_text(encoding="utf-8")
    # Strip reviewer-facing <!-- comments --> so they never reach the model.
    stripped = re.sub(r"<!--.*?-->", "", raw, flags=re.S)
    return re.sub(r"\n{3,}", "\n\n", stripped).strip()


def build_assistant(base_url: str, secret: str | None) -> dict:
    webhook = {"url": f"{base_url.rstrip('/')}/vapi/webhook", "timeoutSeconds": 20}
    if secret:
        webhook["headers"] = {"x-vapi-secret": secret}

    tools = json.loads((ROOT / "agent" / "tools.json").read_text(encoding="utf-8"))
    for tool in tools:
        tool["server"] = webhook
    # Vapi's built-in hang-up tool, so the agent can end the call gracefully.
    tools.append({"type": "endCall"})

    return {
        "name": ASSISTANT_NAME,
        "firstMessage": (
            "Hi, thanks for calling Maple Grove Family Medicine. This is Ava. "
            "I can get you registered as a new patient. It only takes a few minutes. "
            "To start, could I have your first and last name?"
        ),
        # Don't talk over the caller if they start speaking during the greeting.
        "firstMessageInterruptionsEnabled": True,
        "model": {
            "provider": "openai",
            "model": os.getenv("LLM_MODEL", "gpt-4o"),
            "temperature": 0.3,  # low: consistent, accurate data capture
            "messages": [{"role": "system", "content": load_prompt()}],
            "tools": tools,
        },
        "voice": {
            "provider": "11labs",
            "voiceId": os.getenv("VOICE_ID", "21m00Tcm4TlvDq8ikWAM"),
            "model": "eleven_turbo_v2_5",  # low latency and multilingual (English + Spanish)
        },
        "transcriber": {
            "provider": "deepgram",
            "model": "nova-3",
            "language": "multi",  # code-switching English/Spanish
        },
        "server": webhook,
        "serverMessages": ["end-of-call-report"],
        "endCallMessage": "Thanks for calling Maple Grove. Take care!",
        "voicemailMessage": "Hi, this is Ava from Maple Grove Family Medicine. Please call us back when you have a moment.",
        "silenceTimeoutSeconds": 30,
        "maxDurationSeconds": 900,
    }


def _client(key: str) -> httpx.Client:
    return httpx.Client(base_url=API, headers={"Authorization": f"Bearer {key}"}, timeout=30)


def _check(resp: httpx.Response) -> dict | list:
    if resp.status_code >= 400:
        print(f"Vapi API error {resp.status_code} on {resp.request.method} {resp.request.url}:\n{resp.text}", file=sys.stderr)
        sys.exit(1)
    return resp.json()


# Nice-to-have settings. If Vapi's API ever rejects one (schema drift), we drop
# it and retry rather than failing the whole setup.
OPTIONAL_KEYS = ("firstMessageInterruptionsEnabled", "voicemailMessage", "silenceTimeoutSeconds")


def _save_assistant(client: httpx.Client, assistant: dict, existing_id: str | None) -> dict:
    def send(body: dict) -> httpx.Response:
        if existing_id:
            return client.patch(f"/assistant/{existing_id}", json=body)
        return client.post("/assistant", json=body)

    resp = send(assistant)
    if resp.status_code == 400:
        rejected = [k for k in OPTIONAL_KEYS if k in resp.text]
        if rejected:
            print(f"Vapi rejected {rejected}; retrying without them")
            resp = send({k: v for k, v in assistant.items() if k not in rejected})
    return _check(resp)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="write agent/vapi_assistant.json only")
    parser.add_argument("--buy-number", metavar="AREA_CODE", help="try to create a free Vapi US number")
    args = parser.parse_args()

    base_url = os.getenv("PUBLIC_BASE_URL", "https://YOUR-APP.up.railway.app")
    assistant = build_assistant(base_url, os.getenv("VAPI_SECRET"))

    out = ROOT / "agent" / "vapi_assistant.json"
    redacted = json.loads(json.dumps(assistant))
    for obj in [redacted["server"], *[t.get("server", {}) for t in redacted["model"]["tools"]]]:
        if "headers" in obj:
            obj["headers"] = {"x-vapi-secret": "<VAPI_SECRET>"}
    out.write_text(json.dumps(redacted, indent=2), encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)} (secret redacted)")
    if args.dry_run:
        return

    key = os.getenv("VAPI_API_KEY")
    if not key:
        sys.exit("VAPI_API_KEY is not set")
    if "YOUR-APP" in base_url:
        sys.exit("PUBLIC_BASE_URL is not set")

    with _client(key) as client:
        existing = next((a for a in _check(client.get("/assistant", params={"limit": 100}))
                         if a.get("name") == ASSISTANT_NAME), None)
        result = _save_assistant(client, assistant, existing["id"] if existing else None)
        print(f"{'updated' if existing else 'created'} assistant {result['id']}")
        assistant_id = result["id"]

        if args.buy_number:
            resp = client.post("/phone-number", json={
                "provider": "vapi", "numberDesiredAreaCode": args.buy_number,
                "name": "Patient Intake", "assistantId": assistant_id,
            })
            if resp.status_code < 400:
                num = resp.json()
                print(f"created phone number {num.get('number') or '(provisioning...)'} id={num['id']}")
                return
            print(f"could not create number automatically ({resp.status_code}): {resp.text}\n"
                  "Create one in the Vapi dashboard: Phone Numbers -> Create Phone Number -> Free Vapi Number.")

        numbers = _check(client.get("/phone-number"))
        wanted = os.getenv("VAPI_PHONE_NUMBER_ID")
        targets = [n for n in numbers if n["id"] == wanted] if wanted else numbers
        if len(targets) != 1:
            print(f"found {len(numbers)} phone number(s); set VAPI_PHONE_NUMBER_ID to pick one, "
                  "or attach the assistant in the dashboard (Phone Numbers -> Inbound -> Assistant).")
            for n in numbers:
                print(f"  {n['id']}  {n.get('number')}  {n.get('name')}")
            return
        number = _check(client.patch(f"/phone-number/{targets[0]['id']}", json={"assistantId": assistant_id}))
        print(f"attached assistant to {number.get('number')} -- call it!")


if __name__ == "__main__":
    main()
