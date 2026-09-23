"""Bonus: mock appointment availability + booking.

Availability is generated (next 7 business days, fixed times, two providers);
bookings are real rows, so a booked slot disappears from future offers."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Appointment

SLOT_TIMES = (time(9, 0), time(10, 30), time(13, 0), time(15, 30))
PROVIDERS = ("Dr. Priya Patel", "Dr. Marcus Nguyen")
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


@dataclass
class Slot:
    slot_id: str
    starts_at: datetime
    provider_name: str

    def spoken(self) -> str:
        local = self.starts_at.astimezone(ZoneInfo(settings.clinic_timezone))
        hour = local.strftime("%I").lstrip("0")
        minute = "" if local.minute == 0 else local.strftime(":%M")
        return f"{local.strftime('%A, %B')} {local.day} at {hour}{minute} {local.strftime('%p')} with {self.provider_name}"


def _all_slots(today: date) -> list[Slot]:
    tz = ZoneInfo(settings.clinic_timezone)
    slots: list[Slot] = []
    day = today
    business_days = 0
    while business_days < 7:
        day += timedelta(days=1)
        if day.weekday() >= 5:
            continue
        business_days += 1
        for i, t in enumerate(SLOT_TIMES):
            local = datetime.combine(day, t, tzinfo=tz)
            slots.append(Slot(
                slot_id=local.strftime("%Y%m%d-%H%M"),
                starts_at=local.astimezone(timezone.utc),
                provider_name=PROVIDERS[(day.toordinal() + i) % len(PROVIDERS)],
            ))
    return slots


def available_slots(session: Session, preferred_day: str | None = None, limit: int = 3) -> list[Slot]:
    today = datetime.now(ZoneInfo(settings.clinic_timezone)).date()
    booked = set(session.scalars(select(Appointment.slot_id)))
    slots = [s for s in _all_slots(today) if s.slot_id not in booked]

    if preferred_day:
        key = preferred_day.strip().lower()
        tz = ZoneInfo(settings.clinic_timezone)
        if key in ("tomorrow", "mañana", "manana"):
            target = today + timedelta(days=1)
            filtered = [s for s in slots if s.starts_at.astimezone(tz).date() == target]
        else:
            day_name = next((d for d in WEEKDAYS if d.startswith(key[:3])), None)
            filtered = [s for s in slots if day_name and s.starts_at.astimezone(tz).strftime("%A").lower() == day_name]
        if "morning" in key:
            filtered = [s for s in (filtered or slots) if s.starts_at.astimezone(tz).hour < 12]
        elif "afternoon" in key:
            filtered = [s for s in (filtered or slots) if s.starts_at.astimezone(tz).hour >= 12]
        slots = filtered or slots
    return slots[:limit]


def book(session: Session, patient_id: uuid.UUID, slot_id: str, reason: str | None = None) -> Appointment:
    today = datetime.now(ZoneInfo(settings.clinic_timezone)).date()
    slot = next((s for s in _all_slots(today) if s.slot_id == slot_id), None)
    if slot is None:
        raise ValueError("that time slot doesn't exist")
    if session.scalar(select(Appointment).where(Appointment.slot_id == slot_id)):
        raise ValueError("that time slot was just taken")
    appt = Appointment(patient_id=patient_id, slot_id=slot_id, starts_at=slot.starts_at,
                       provider_name=slot.provider_name, reason=reason)
    session.add(appt)
    session.commit()
    session.refresh(appt)
    return appt


def spoken_for_slot_id(slot_id: str) -> str | None:
    today = datetime.now(ZoneInfo(settings.clinic_timezone)).date()
    slot = next((s for s in _all_slots(today) if s.slot_id == slot_id), None)
    return slot.spoken() if slot else None
