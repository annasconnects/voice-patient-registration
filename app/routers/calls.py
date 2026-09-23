"""Read-only views over call transcripts and appointments (bonus features)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Appointment, CallLog, iso_utc
from app.responses import ApiError, ok
from app.routers.patients import _get_or_404, _parse_id

router = APIRouter(tags=["calls"])


def _call(c: CallLog, include_transcript: bool = True) -> dict:
    out = {
        "call_id": c.call_id,
        "patient_id": str(c.patient_id) if c.patient_id else None,
        "caller_number": c.caller_number,
        "outcome": c.outcome,
        "ended_reason": c.ended_reason,
        "summary": c.summary,
        "started_at": iso_utc(c.started_at),
        "ended_at": iso_utc(c.ended_at),
        "collected_payload": c.collected_payload,
    }
    if include_transcript:
        out["transcript"] = c.transcript
    return out


def _appt(a: Appointment) -> dict:
    return {
        "appointment_id": str(a.appointment_id),
        "patient_id": str(a.patient_id),
        "slot_id": a.slot_id,
        "starts_at": iso_utc(a.starts_at),
        "provider_name": a.provider_name,
        "reason": a.reason,
    }


@router.get("/calls")
def list_calls(limit: int = Query(50, ge=1, le=200), session: Session = Depends(get_session)):
    rows = session.scalars(select(CallLog).order_by(CallLog.started_at.desc()).limit(limit))
    return ok([_call(c, include_transcript=False) for c in rows])


@router.get("/calls/{call_id}")
def get_call(call_id: str, session: Session = Depends(get_session)):
    c = session.get(CallLog, call_id)
    if c is None:
        raise ApiError(404, "not_found", f"call {call_id} not found")
    return ok(_call(c))


@router.get("/patients/{patient_id}/calls")
def patient_calls(patient_id: str, session: Session = Depends(get_session)):
    pid = _parse_id(patient_id)
    _get_or_404(session, pid)
    rows = session.scalars(select(CallLog).where(CallLog.patient_id == pid).order_by(CallLog.started_at.desc()))
    return ok([_call(c) for c in rows])


@router.get("/patients/{patient_id}/appointments")
def patient_appointments(patient_id: str, session: Session = Depends(get_session)):
    pid = _parse_id(patient_id)
    _get_or_404(session, pid)
    rows = session.scalars(select(Appointment).where(Appointment.patient_id == pid).order_by(Appointment.starts_at))
    return ok([_appt(a) for a in rows])
