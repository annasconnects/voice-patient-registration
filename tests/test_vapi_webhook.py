import json
from dataclasses import replace

from sqlalchemy.exc import OperationalError

from tests.conftest import VALID, tool_call


def _result(resp):
    assert resp.status_code == 200, resp.text
    results = resp.json()["results"]
    assert len(results) == 1
    return json.loads(results[0]["result"])


def test_validate_fields_flags_specific_problems(client):
    r = _result(client.post("/vapi/webhook", json=tool_call("validate_fields", {
        "date_of_birth": "01/01/2999", "phone_number": "555", "state": "texas", "zip_code": "78701",
    })))
    assert r["status"] == "invalid"
    assert set(r["invalid"]) == {"date_of_birth", "phone_number"}
    assert r["valid"]["state"] == "TX"


def test_validate_fields_returns_spoken_dob(client):
    r = _result(client.post("/vapi/webhook", json=tool_call("validate_fields", {"date_of_birth": "03/05/1990"})))
    assert r["status"] == "ok"
    assert r["valid"]["date_of_birth"] == {"value": "03/05/1990", "spoken": "March 5, 1990"}


def test_lookup_existing_patient_by_phone(client):
    r = _result(client.post("/vapi/webhook", json=tool_call("lookup_patient_by_phone", {"phone_number": "512-555-0143"})))
    assert r["status"] == "found"
    assert r["patients"][0]["first_name"] == "Jane"
    assert "date_of_birth" not in r["patients"][0]  # don't leak details over the phone


def test_lookup_not_found(client):
    r = _result(client.post("/vapi/webhook", json=tool_call("lookup_patient_by_phone", {"phone_number": "4155559999"})))
    assert r["status"] == "not_found"


def test_register_then_end_of_call_links_transcript(client):
    r = _result(client.post("/vapi/webhook", json=tool_call("register_patient", {**VALID, "email": ""}, call_id="c-42")))
    assert r["status"] == "saved"
    pid = r["patient_id"]
    assert client.get(f"/patients/{pid}").json()["data"]["email"] is None

    # A retry of the same tool in the same call must not create a duplicate.
    again = _result(client.post("/vapi/webhook", json=tool_call("register_patient", VALID, call_id="c-42")))
    assert again["patient_id"] == pid
    assert len(client.get("/patients", params={"last_name": "Davis"}).json()["data"]) == 1

    report = {"message": {
        "type": "end-of-call-report", "endedReason": "assistant-ended-call",
        "call": {"id": "c-42", "customer": {"number": "+14155552671"}},
        "artifact": {"transcript": "AI: Hi...\nUser: I'm Maria Davis..."},
        "analysis": {"summary": "Registered Maria Davis."},
    }}
    assert client.post("/vapi/webhook", json=report).status_code == 200
    calls = client.get(f"/patients/{pid}/calls").json()["data"]
    assert calls[0]["transcript"].startswith("AI: Hi")
    assert calls[0]["outcome"] == "created"
    assert calls[0]["summary"] == "Registered Maria Davis."


def test_dropped_call_is_logged_without_patient(client):
    report = {"message": {"type": "end-of-call-report", "endedReason": "customer-ended-call",
                          "call": {"id": "c-drop"}, "artifact": {"transcript": "AI: Hi\nUser: my name is"}}}
    client.post("/vapi/webhook", json=report)
    call = client.get("/calls/c-drop").json()["data"]
    assert call["outcome"] == "none" and call["patient_id"] is None


def test_register_invalid_returns_field_errors(client):
    r = _result(client.post("/vapi/webhook", json=tool_call("register_patient", {**VALID, "phone_number": "5551234"})))
    assert r["status"] == "invalid"
    assert r["errors"][0]["field"] == "phone_number"


def test_register_db_failure_is_graceful(client, monkeypatch):
    from app.services import patient_service

    def boom(*a, **k):
        raise OperationalError("INSERT", {}, Exception("db down"))

    monkeypatch.setattr(patient_service, "create_patient", boom)
    r = _result(client.post("/vapi/webhook", json=tool_call("register_patient", VALID)))
    assert r["status"] == "save_failed"
    assert "next_step" in r


def test_handler_crash_never_returns_500(client, monkeypatch):
    from app.services import voice_tools

    monkeypatch.setitem(voice_tools.TOOL_HANDLERS, "validate_fields", lambda *a: 1 / 0)
    r = _result(client.post("/vapi/webhook", json=tool_call("validate_fields", {})))
    assert r["status"] == "error"


def test_update_existing_patient(client):
    pid = _result(client.post("/vapi/webhook", json=tool_call("lookup_patient_by_phone", {"phone_number": "5125550143"})))["patients"][0]["patient_id"]
    r = _result(client.post("/vapi/webhook", json=tool_call("update_patient", {"patient_id": pid, "city": "Round Rock", "zip_code": "78664"})))
    assert r["status"] == "saved" and set(r["updated_fields"]) == {"city", "zip_code"}
    assert client.get(f"/patients/{pid}").json()["data"]["city"] == "Round Rock"


def test_string_arguments_and_legacy_shape(client):
    body = {"message": {"type": "tool-calls", "call": {"id": "c-9"},
                        "toolCallList": [{"id": "t1", "name": "lookup_patient_by_phone",
                                          "arguments": json.dumps({"phone_number": "3055550178"})}]}}
    r = _result(client.post("/vapi/webhook", json=body))
    assert r["status"] == "found"


def test_unknown_tool(client):
    assert _result(client.post("/vapi/webhook", json=tool_call("nope", {})))["status"] == "error"


def test_appointments_flow(client):
    pid = _result(client.post("/vapi/webhook", json=tool_call("register_patient", VALID, call_id="c-appt")))["patient_id"]
    slots = _result(client.post("/vapi/webhook", json=tool_call("get_available_appointments", {"preferred_day": "afternoon"})))
    assert slots["status"] == "ok" and 1 <= len(slots["slots"]) <= 3
    slot = slots["slots"][0]["slot_id"]
    booked = _result(client.post("/vapi/webhook", json=tool_call("book_appointment", {"patient_id": pid, "slot_id": slot})))
    assert booked["status"] == "booked"
    again = _result(client.post("/vapi/webhook", json=tool_call("book_appointment", {"patient_id": pid, "slot_id": slot})))
    assert again["status"] == "unavailable"
    assert client.get(f"/patients/{pid}/appointments").json()["data"][0]["slot_id"] == slot


def test_webhook_secret(client, monkeypatch):
    from app.routers import vapi

    monkeypatch.setattr(vapi, "settings", replace(vapi.settings, vapi_secret="s3cret"))
    body = tool_call("lookup_patient_by_phone", {"phone_number": "5125550143"})
    assert client.post("/vapi/webhook", json=body).status_code == 401
    assert client.post("/vapi/webhook", json=body, headers={"x-vapi-secret": "s3cret"}).status_code == 200


def test_other_events_acknowledged(client):
    assert client.post("/vapi/webhook", json={"message": {"type": "status-update"}}).status_code == 200
