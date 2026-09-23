"""ORM models. Constraints here are the last line of defence; the Pydantic
schemas in `schemas.py` do the user-friendly validation first.

Only portable SQL is used in CHECK constraints so the same schema runs on
Postgres (production) and SQLite (local dev / tests)."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    Uuid,
    JSON,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.validators import SEX_VALUES, US_STATE_CODES


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime | None) -> str | None:
    """ISO-8601 in UTC with a Z suffix. SQLite drops tzinfo, but every value is
    written in UTC, so a naive value is re-tagged as UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _in_list(column: str, values) -> str:
    quoted = ", ".join(f"'{v}'" for v in sorted(values))
    return f"{column} IN ({quoted})"


class Patient(Base):
    __tablename__ = "patients"

    patient_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    first_name: Mapped[str] = mapped_column(String(50), nullable=False)
    last_name: Mapped[str] = mapped_column(String(50), nullable=False)
    date_of_birth: Mapped[date] = mapped_column(Date, nullable=False)
    sex: Mapped[str] = mapped_column(String(20), nullable=False)
    phone_number: Mapped[str] = mapped_column(String(10), nullable=False)  # 10 digits, no formatting
    email: Mapped[str | None] = mapped_column(String(254))

    address_line_1: Mapped[str] = mapped_column(String(200), nullable=False)
    address_line_2: Mapped[str | None] = mapped_column(String(100))
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(2), nullable=False)
    zip_code: Mapped[str] = mapped_column(String(10), nullable=False)  # 12345 or 12345-6789

    insurance_provider: Mapped[str | None] = mapped_column(String(100))
    insurance_member_id: Mapped[str | None] = mapped_column(String(50))
    preferred_language: Mapped[str] = mapped_column(String(50), nullable=False, default="English")
    emergency_contact_name: Mapped[str | None] = mapped_column(String(100))
    emergency_contact_phone: Mapped[str | None] = mapped_column(String(10))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    appointments: Mapped[list["Appointment"]] = relationship(back_populates="patient")

    __table_args__ = (
        CheckConstraint("length(first_name) BETWEEN 1 AND 50", name="ck_first_name_len"),
        CheckConstraint("length(last_name) BETWEEN 1 AND 50", name="ck_last_name_len"),
        CheckConstraint(_in_list("sex", SEX_VALUES), name="ck_sex_enum"),
        CheckConstraint("length(phone_number) = 10", name="ck_phone_len"),
        CheckConstraint(
            "emergency_contact_phone IS NULL OR length(emergency_contact_phone) = 10",
            name="ck_ec_phone_len",
        ),
        CheckConstraint(_in_list("state", US_STATE_CODES), name="ck_state_code"),
        CheckConstraint("length(zip_code) IN (5, 10)", name="ck_zip_len"),
        CheckConstraint("length(city) BETWEEN 1 AND 100", name="ck_city_len"),
        CheckConstraint("date_of_birth >= '1900-01-01'", name="ck_dob_min"),
        Index("ix_patients_last_name", "last_name"),
        Index("ix_patients_phone_number", "phone_number"),
        Index("ix_patients_date_of_birth", "date_of_birth"),
        Index("ix_patients_deleted_at", "deleted_at"),
    )


class CallLog(Base):
    """One row per phone call: transcript + summary, linked to the patient the
    call created/updated (bonus: call transcript storage)."""

    __tablename__ = "call_logs"

    call_id: Mapped[str] = mapped_column(String(100), primary_key=True)  # Vapi call id
    patient_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("patients.patient_id"), index=True
    )
    caller_number: Mapped[str | None] = mapped_column(String(20))
    outcome: Mapped[str | None] = mapped_column(String(30))  # created / updated / none
    ended_reason: Mapped[str | None] = mapped_column(String(100))
    summary: Mapped[str | None] = mapped_column(Text)
    transcript: Mapped[str | None] = mapped_column(Text)
    collected_payload: Mapped[dict | None] = mapped_column(JSON)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Appointment(Base):
    """Bonus: first-appointment booking against mock availability."""

    __tablename__ = "appointments"

    appointment_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    patient_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("patients.patient_id"), nullable=False, index=True
    )
    slot_id: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    provider_name: Mapped[str] = mapped_column(String(100), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    patient: Mapped[Patient] = relationship(back_populates="appointments")
