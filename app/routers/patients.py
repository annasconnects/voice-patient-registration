"""REST API for patient records."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, Query
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app import validators as v
from app.config import settings
from app.db import get_session
from app.models import iso_utc
from app.responses import ApiError, ok, pydantic_errors_to_details
from app.schemas import PatientCreate, PatientOut, PatientUpdate
from app.services import patient_service as svc

router = APIRouter(prefix="/patients", tags=["patients"])


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Write endpoints require X-API-Key only when API_KEY is configured."""
    if settings.api_key and x_api_key != settings.api_key:
        raise ApiError(401, "unauthorized", "missing or invalid X-API-Key header")


def _parse_id(patient_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(patient_id)
    except ValueError:
        raise ApiError(400, "invalid_id", "patient_id must be a valid UUID")


def _serialize(patient) -> dict:
    return PatientOut.model_validate(patient).model_dump(mode="json")


def _validate(model, payload: Any):
    if not isinstance(payload, dict):
        raise ApiError(400, "bad_request", "request body must be a JSON object")
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise ApiError(422, "validation_error", "one or more fields are invalid",
                       pydantic_errors_to_details(exc.errors()))


def _get_or_404(session: Session, pid: uuid.UUID):
    try:
        return svc.get_patient(session, pid)
    except svc.PatientNotFound:
        raise ApiError(404, "not_found", f"patient {pid} not found")


@router.get("")
def list_patients(
    last_name: str | None = Query(default=None, max_length=50),
    date_of_birth: str | None = Query(default=None, description="MM/DD/YYYY or YYYY-MM-DD"),
    phone_number: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
):
    try:
        dob = v.parse_date_of_birth(date_of_birth) if date_of_birth else None
        phone = v.normalize_phone(phone_number) if phone_number else None
    except ValueError as exc:
        raise ApiError(400, "invalid_query", str(exc))
    patients = svc.list_patients(session, last_name, dob, phone, limit, offset)
    return ok([_serialize(p) for p in patients])


@router.get("/{patient_id}")
def get_patient(patient_id: str, session: Session = Depends(get_session)):
    return ok(_serialize(_get_or_404(session, _parse_id(patient_id))))


@router.post("", status_code=201, dependencies=[Depends(require_api_key)])
def create_patient(payload: Any = Body(...), session: Session = Depends(get_session)):
    data = _validate(PatientCreate, payload)
    return ok(_serialize(svc.create_patient(session, data)), 201)


@router.put("/{patient_id}", dependencies=[Depends(require_api_key)])
def update_patient(patient_id: str, payload: Any = Body(...), session: Session = Depends(get_session)):
    pid = _parse_id(patient_id)
    data = _validate(PatientUpdate, payload)
    try:
        return ok(_serialize(svc.update_patient(session, pid, data)))
    except svc.PatientNotFound:
        raise ApiError(404, "not_found", f"patient {pid} not found")


@router.delete("/{patient_id}", dependencies=[Depends(require_api_key)])
def delete_patient(patient_id: str, session: Session = Depends(get_session)):
    pid = _parse_id(patient_id)
    try:
        patient = svc.soft_delete_patient(session, pid)
    except svc.PatientNotFound:
        raise ApiError(404, "not_found", f"patient {pid} not found")
    return ok({"patient_id": str(patient.patient_id), "deleted_at": iso_utc(patient.deleted_at)})
