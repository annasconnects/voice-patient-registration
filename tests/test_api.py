import uuid
from dataclasses import replace

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Patient
from tests.conftest import VALID


def _create(client, **overrides):
    r = client.post("/patients", json={**VALID, **overrides})
    assert r.status_code == 201, r.text
    return r.json()["data"]


def test_seed_data_present(client):
    r = client.get("/patients")
    assert r.status_code == 200
    body = r.json()
    assert body["error"] is None
    assert {p["last_name"] for p in body["data"]} == {"Doe", "O'Neil-Ramirez"}


def test_create_normalizes_and_returns_record(client):
    p = _create(client, state="california", sex="female", email="Maria.Davis@Example.com")
    assert uuid.UUID(p["patient_id"])
    assert p["phone_number"] == "4155552671"
    assert p["state"] == "CA"
    assert p["sex"] == "Female"
    assert p["email"] == "maria.davis@example.com"
    assert p["date_of_birth"] == "03/05/1990"
    assert p["preferred_language"] == "English"
    assert p["created_at"].endswith("Z")


def test_create_validation_errors_are_field_specific(client):
    r = client.post("/patients", json={**VALID, "phone_number": "555", "date_of_birth": "01/01/2999",
                                       "state": "ZZ", "zip_code": "12", "email": "nope"})
    assert r.status_code == 422
    body = r.json()
    assert body["data"] is None
    fields = {d["field"] for d in body["error"]["details"]}
    assert fields == {"phone_number", "date_of_birth", "state", "zip_code", "email"}


def test_create_missing_required_and_unknown_fields(client):
    payload = {k: v for k, v in VALID.items() if k != "city"}
    r = client.post("/patients", json={**payload, "ssn": "123"})
    assert r.status_code == 422
    fields = {d["field"] for d in r.json()["error"]["details"]}
    assert {"city", "ssn"} <= fields


def test_bad_json_is_400(client):
    r = client.post("/patients", content="{not json", headers={"content-type": "application/json"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_request"


def test_get_by_id_and_404_and_bad_uuid(client):
    p = _create(client)
    assert client.get(f"/patients/{p['patient_id']}").json()["data"]["last_name"] == "Davis"
    assert client.get(f"/patients/{uuid.uuid4()}").status_code == 404
    assert client.get("/patients/not-a-uuid").status_code == 400


def test_filters(client):
    _create(client)
    assert len(client.get("/patients", params={"last_name": "davis"}).json()["data"]) == 1
    assert len(client.get("/patients", params={"phone_number": "415-555-2671"}).json()["data"]) == 1
    assert len(client.get("/patients", params={"date_of_birth": "1990-03-05"}).json()["data"]) == 1
    assert len(client.get("/patients", params={"date_of_birth": "03/05/1990"}).json()["data"]) == 1
    assert client.get("/patients", params={"phone_number": "123"}).status_code == 400


def test_partial_update(client):
    p = _create(client)
    r = client.put(f"/patients/{p['patient_id']}", json={"last_name": "Davies", "email": "m@example.com"})
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["last_name"] == "Davies" and d["first_name"] == "Maria" and d["email"] == "m@example.com"
    assert d["updated_at"] >= p["updated_at"]


def test_update_rejects_null_required_empty_body_and_bad_values(client):
    p = _create(client)
    pid = p["patient_id"]
    assert client.put(f"/patients/{pid}", json={"first_name": None}).status_code == 422
    assert client.put(f"/patients/{pid}", json={}).status_code == 422
    assert client.put(f"/patients/{pid}", json={"zip_code": "abc"}).status_code == 422
    assert client.put(f"/patients/{uuid.uuid4()}", json={"city": "Austin"}).status_code == 404


def test_soft_delete(client):
    p = _create(client)
    pid = p["patient_id"]
    r = client.delete(f"/patients/{pid}")
    assert r.status_code == 200 and r.json()["data"]["deleted_at"]
    assert client.get(f"/patients/{pid}").status_code == 404
    assert pid not in {x["patient_id"] for x in client.get("/patients").json()["data"]}
    assert client.delete(f"/patients/{pid}").status_code == 404
    with SessionLocal() as s:  # row still exists, just flagged
        row = s.scalar(select(Patient).where(Patient.patient_id == uuid.UUID(pid)))
        assert row is not None and row.deleted_at is not None


def test_api_key_enforced_when_configured(client, monkeypatch):
    from app.routers import patients as router

    monkeypatch.setattr(router, "settings", replace(router.settings, api_key="secret"))
    assert client.post("/patients", json=VALID).status_code == 401
    assert client.post("/patients", json=VALID, headers={"X-API-Key": "secret"}).status_code == 201
    assert client.get("/patients").status_code == 200  # reads stay open


def test_unknown_route_uses_envelope(client):
    r = client.get("/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_health_and_dashboard(client):
    assert client.get("/health").json()["data"]["database"] == "ok"
    assert "Patient Registrations" in client.get("/dashboard").text


def test_data_survives_new_engine(client):
    """Persistence: a fresh engine (like a server restart) sees earlier writes."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.config import settings

    p = _create(client)
    fresh = create_engine(settings.database_url)
    with Session(fresh) as s:
        assert s.get(Patient, uuid.UUID(p["patient_id"])) is not None
    fresh.dispose()
