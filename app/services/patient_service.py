"""Service layer: the only code that touches the patients table. Both the REST
routes and the voice-agent tool handlers call these functions, so business
rules live in exactly one place."""
from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from datetime import date
from typing import TypeVar

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.logging_config import log_event
from app.models import Patient, utcnow
from app.schemas import PatientCreate, PatientUpdate

logger = logging.getLogger("patient_service")
T = TypeVar("T")


class PatientNotFound(Exception):
    pass


def _with_retry(session: Session, fn: Callable[[], T], attempts: int = 2) -> T:
    """Retry once on transient connection errors (e.g. the DB host dropped an
    idle connection). Anything else bubbles up to the caller."""
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except OperationalError:
            session.rollback()
            if attempt == attempts:
                raise
            log_event(logger, "db_retry", {"attempt": attempt}, logging.WARNING)
            time.sleep(0.3)
    raise RuntimeError("unreachable")


def _active():
    return select(Patient).where(Patient.deleted_at.is_(None))


def create_patient(session: Session, data: PatientCreate) -> Patient:
    def _do() -> Patient:
        patient = Patient(**data.model_dump())
        session.add(patient)
        session.commit()
        session.refresh(patient)
        return patient

    patient = _with_retry(session, _do)
    log_event(logger, "patient_created", {"patient_id": str(patient.patient_id), **data.model_dump(mode="json")})
    return patient


def get_patient(session: Session, patient_id: uuid.UUID) -> Patient:
    patient = session.scalar(_active().where(Patient.patient_id == patient_id))
    if patient is None:
        raise PatientNotFound(str(patient_id))
    return patient


def list_patients(
    session: Session,
    last_name: str | None = None,
    date_of_birth: date | None = None,
    phone_number: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Patient]:
    stmt = _active()
    if last_name:
        stmt = stmt.where(func.lower(Patient.last_name) == last_name.strip().lower())
    if date_of_birth:
        stmt = stmt.where(Patient.date_of_birth == date_of_birth)
    if phone_number:
        stmt = stmt.where(Patient.phone_number == phone_number)
    stmt = stmt.order_by(Patient.created_at.desc()).limit(limit).offset(offset)
    return list(session.scalars(stmt))


def find_by_phone(session: Session, phone_number: str) -> list[Patient]:
    return list_patients(session, phone_number=phone_number, limit=5)


def update_patient(session: Session, patient_id: uuid.UUID, data: PatientUpdate) -> Patient:
    changes = data.model_dump(exclude_unset=True)

    def _do() -> Patient:
        patient = get_patient(session, patient_id)
        for key, value in changes.items():
            setattr(patient, key, value)
        patient.updated_at = utcnow()
        session.commit()
        session.refresh(patient)
        return patient

    patient = _with_retry(session, _do)
    log_event(logger, "patient_updated", {"patient_id": str(patient_id), "changes": data.model_dump(mode="json", exclude_unset=True)})
    return patient


def soft_delete_patient(session: Session, patient_id: uuid.UUID) -> Patient:
    def _do() -> Patient:
        patient = get_patient(session, patient_id)
        now = utcnow()
        patient.deleted_at = now
        patient.updated_at = now
        session.commit()
        session.refresh(patient)
        return patient

    patient = _with_retry(session, _do)
    log_event(logger, "patient_soft_deleted", {"patient_id": str(patient_id)})
    return patient
