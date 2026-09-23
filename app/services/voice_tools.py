"""Handlers for the functions the voice agent (LLM) can call.

Each handler takes the tool arguments plus call context and returns a small
JSON-serialisable dict. Results are written for the LLM to act on: every
outcome carries a `status` and, where useful, a `next_step` hint, so the model
never has to guess what to say (and never goes silent on failure)."""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import validators as v
from app.logging_config import log_event
from app.models import CallLog, utcnow
from app.responses import pydantic_errors_to_details
from app.schemas import OPTIONAL_FIELDS, REQUIRED_FIELDS, PatientCreate, PatientUpdate
from app.services import appointment_service, patient_service

logger = logging.getLogger("voice_tools")
PATIENT_FIELDS = REQUIRED_FIELDS + OPTIONAL_FIELDS


@dataclass
class CallContext:
    call_id: str | None = None
    caller_number: str | None = None


def _patient_fields(args: dict[str, Any]) -> dict[str, Any]:
    """Keep only known patient fields and drop empty values the LLM sends for
    fields the caller didn't provide."""
    out = {}
    for key in PATIENT_FIELDS:
        value = args.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        out[key] = value
    return out


def _spoken_dob(dob) -> str:
    return f"{dob.strftime('%B')} {dob.day}, {dob.year}"


def _link_call(session: Session, ctx: CallContext, patient_id: uuid.UUID, outcome: str, payload: dict) -> None:
    """Record which patient this call touched. Never fails the tool call."""
    if not ctx.call_id:
        return
    try:
        log = session.get(CallLog, ctx.call_id) or CallLog(call_id=ctx.call_id, caller_number=ctx.caller_number)
        log.patient_id = patient_id
        log.outcome = outcome
        log.collected_payload = payload
        session.add(log)
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        logger.exception("call_log_link_failed")


# --------------------------------------------------------------------------- tools


def validate_fields(session: Session, ctx: CallContext, args: dict) -> dict:
    """Check any subset of fields mid-conversation so the agent can re-ask for
    a bad value right away instead of at the end."""
    fields = _patient_fields(args)
    valid, invalid = {}, {}
    for key, value in fields.items():
        try:
            parsed = PatientUpdate.model_validate({key: value})
            normalized = getattr(parsed, key)
            if key == "date_of_birth":
                valid[key] = {"value": v.format_dob(normalized), "spoken": _spoken_dob(normalized)}
            else:
                valid[key] = normalized
        except ValidationError as exc:
            invalid[key] = pydantic_errors_to_details(exc.errors())[0]["message"]
    result = {"status": "ok" if not invalid else "invalid", "valid": valid, "invalid": invalid}
    if invalid:
        result["next_step"] = "Tell the caller briefly what was wrong with each invalid field and ask for just that field again."
    return result


def lookup_patient_by_phone(session: Session, ctx: CallContext, args: dict) -> dict:
    try:
        phone = v.normalize_phone(args.get("phone_number", ""))
    except ValueError as exc:
        return {"status": "invalid", "invalid": {"phone_number": str(exc)}}
    try:
        matches = patient_service.find_by_phone(session, phone)
    except SQLAlchemyError:
        session.rollback()
        logger.exception("lookup_failed")
        return {"status": "error", "next_step": "Lookup is unavailable. Continue registration as a new patient without mentioning it."}
    if not matches:
        return {"status": "not_found", "next_step": "No existing record. Continue collecting the remaining fields."}
    # Deliberately return only names + ids (not DOB/address) so an existing
    # record's details aren't read out to whoever knows the phone number.
    return {
        "status": "found",
        "patients": [
            {"patient_id": str(p.patient_id), "first_name": p.first_name, "last_name": p.last_name}
            for p in matches
        ],
        "next_step": "Say: 'It looks like we already have a record for <first> <last>. Would you like to update your information instead?' Then follow the caller's choice.",
    }


def register_patient(session: Session, ctx: CallContext, args: dict) -> dict:
    payload = _patient_fields(args)
    log_event(logger, "register_patient_called", {"call_id": ctx.call_id, "payload": payload})

    # Idempotency: if the model calls this twice in one call, don't create a duplicate.
    if ctx.call_id:
        try:
            existing = session.get(CallLog, ctx.call_id)
        except SQLAlchemyError:
            session.rollback()
            existing = None  # DB trouble surfaces below as save_failed
        if existing and existing.outcome == "created" and existing.patient_id:
            return {"status": "saved", "patient_id": str(existing.patient_id), "note": "already saved earlier in this call"}

    try:
        data = PatientCreate.model_validate(payload)
    except ValidationError as exc:
        errors = pydantic_errors_to_details(exc.errors())
        return {
            "status": "invalid",
            "errors": errors,
            "next_step": "Nothing was saved. Ask the caller again for only the listed fields, then call register_patient again with ALL fields.",
        }

    try:
        patient = patient_service.create_patient(session, data)
    except SQLAlchemyError:
        session.rollback()
        logger.exception("register_patient_db_failure")
        return {
            "status": "save_failed",
            "next_step": "Apologize: there was a problem saving on our end. Offer to try once more. If it fails again, tell them their information was not saved, that a staff member will need to finish registration, and end the call politely.",
        }

    final = data.model_dump(mode="json")
    log_event(logger, "registration_complete", {"call_id": ctx.call_id, "patient_id": str(patient.patient_id), "data": final})
    _link_call(session, ctx, patient.patient_id, "created", final)
    return {"status": "saved", "patient_id": str(patient.patient_id), "first_name": patient.first_name}


def update_patient(session: Session, ctx: CallContext, args: dict) -> dict:
    try:
        pid = uuid.UUID(str(args.get("patient_id", "")))
    except ValueError:
        return {"status": "invalid", "errors": [{"field": "patient_id", "message": "unknown patient id; call lookup_patient_by_phone first"}]}
    payload = _patient_fields(args)
    log_event(logger, "update_patient_called", {"call_id": ctx.call_id, "patient_id": str(pid), "payload": payload})
    if not payload:
        return {"status": "invalid", "errors": [{"field": None, "message": "no fields to update were provided"}]}
    try:
        data = PatientUpdate.model_validate(payload)
    except ValidationError as exc:
        return {"status": "invalid", "errors": pydantic_errors_to_details(exc.errors()),
                "next_step": "Nothing was saved. Re-ask only the listed fields."}
    try:
        patient = patient_service.update_patient(session, pid, data)
    except patient_service.PatientNotFound:
        return {"status": "not_found", "next_step": "That record no longer exists. Offer to register them as a new patient."}
    except SQLAlchemyError:
        session.rollback()
        logger.exception("update_patient_db_failure")
        return {"status": "save_failed", "next_step": "Apologize, say the update could not be saved right now, offer one retry, otherwise say staff will follow up."}

    changes = data.model_dump(mode="json", exclude_unset=True)
    log_event(logger, "update_complete", {"call_id": ctx.call_id, "patient_id": str(pid), "changes": changes})
    _link_call(session, ctx, pid, "updated", changes)
    return {"status": "saved", "patient_id": str(pid), "first_name": patient.first_name, "updated_fields": list(changes)}


def get_available_appointments(session: Session, ctx: CallContext, args: dict) -> dict:
    slots = appointment_service.available_slots(session, args.get("preferred_day"))
    if not slots:
        return {"status": "none", "next_step": "Say no times are open this week and that the office will call them."}
    return {
        "status": "ok",
        "slots": [{"slot_id": s.slot_id, "spoken": s.spoken()} for s in slots],
        "next_step": "Offer these times conversationally (at most three). Book with the slot_id they choose.",
    }


def book_appointment(session: Session, ctx: CallContext, args: dict) -> dict:
    try:
        pid = uuid.UUID(str(args.get("patient_id", "")))
        patient_service.get_patient(session, pid)
    except (ValueError, patient_service.PatientNotFound):
        return {"status": "invalid", "next_step": "The patient must be registered before booking."}
    try:
        appt = appointment_service.book(session, pid, str(args.get("slot_id", "")), args.get("reason"))
    except ValueError as exc:
        return {"status": "unavailable", "message": str(exc), "next_step": "Apologize and offer other times via get_available_appointments."}
    except SQLAlchemyError:
        session.rollback()
        logger.exception("book_appointment_db_failure")
        return {"status": "save_failed", "next_step": "Apologize; the booking didn't go through. Their registration is still saved. The office will call to schedule."}
    spoken = appointment_service.spoken_for_slot_id(appt.slot_id)
    log_event(logger, "appointment_booked", {"call_id": ctx.call_id, "patient_id": str(pid), "slot_id": appt.slot_id})
    return {"status": "booked", "appointment_id": str(appt.appointment_id), "spoken": spoken}


TOOL_HANDLERS = {
    "validate_fields": validate_fields,
    "lookup_patient_by_phone": lookup_patient_by_phone,
    "register_patient": register_patient,
    "update_patient": update_patient,
    "get_available_appointments": get_available_appointments,
    "book_appointment": book_appointment,
}


def record_call_end(session: Session, call_id: str, caller_number: str | None, ended_reason: str | None,
                    transcript: str | None, summary: str | None) -> None:
    """Persist the transcript/summary from Vapi's end-of-call report. Runs even
    for calls that dropped mid-way, so abandoned registrations are auditable."""
    log = session.get(CallLog, call_id) or CallLog(call_id=call_id, caller_number=caller_number)
    log.ended_reason = ended_reason
    log.transcript = transcript
    log.summary = summary
    log.ended_at = utcnow()
    if not log.outcome:
        log.outcome = "none"
    session.add(log)
    session.commit()
    log_event(logger, "call_ended", {
        "call_id": call_id, "ended_reason": ended_reason, "outcome": log.outcome,
        "patient_id": str(log.patient_id) if log.patient_id else None,
        "collected_payload": log.collected_payload,
    })
