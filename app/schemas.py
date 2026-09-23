"""Request/response schemas. All server-side validation lives here (via the
helpers in validators.py) so the REST API and the voice tools share one rule set."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, field_serializer, field_validator, model_validator

from app import validators as v

REQUIRED_FIELDS = (
    "first_name", "last_name", "date_of_birth", "sex", "phone_number",
    "address_line_1", "city", "state", "zip_code",
)
OPTIONAL_FIELDS = (
    "email", "address_line_2", "insurance_provider", "insurance_member_id",
    "preferred_language", "emergency_contact_name", "emergency_contact_phone",
)


def _blank_to_none(value: Any) -> Any:
    if isinstance(value, str) and not value.strip():
        return None
    return value


class _PatientFields(BaseModel):
    """Field validators shared by create and update."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator(*OPTIONAL_FIELDS, mode="before", check_fields=False)
    @classmethod
    def _optional_blank(cls, value):
        return _blank_to_none(value)

    @field_validator("first_name", mode="before", check_fields=False)
    @classmethod
    def _first(cls, value):
        return None if value is None else v.normalize_name(value, "first name")

    @field_validator("last_name", mode="before", check_fields=False)
    @classmethod
    def _last(cls, value):
        return None if value is None else v.normalize_name(value, "last name")

    @field_validator("date_of_birth", mode="before", check_fields=False)
    @classmethod
    def _dob(cls, value):
        return None if value is None else v.parse_date_of_birth(value)

    @field_validator("sex", mode="before", check_fields=False)
    @classmethod
    def _sex(cls, value):
        return None if value is None else v.normalize_sex(value)

    @field_validator("phone_number", mode="before", check_fields=False)
    @classmethod
    def _phone(cls, value):
        return None if value is None else v.normalize_phone(value)

    @field_validator("emergency_contact_phone", mode="before", check_fields=False)
    @classmethod
    def _ec_phone(cls, value):
        value = _blank_to_none(value)
        return None if value is None else v.normalize_phone(value, "emergency contact phone")

    @field_validator("address_line_1", mode="before", check_fields=False)
    @classmethod
    def _addr1(cls, value):
        return None if value is None else v.normalize_free_text(value, "street address", 1, 200)

    @field_validator("address_line_2", mode="before", check_fields=False)
    @classmethod
    def _addr2(cls, value):
        value = _blank_to_none(value)
        return None if value is None else v.normalize_free_text(value, "apartment or unit", 1, 100)

    @field_validator("city", mode="before", check_fields=False)
    @classmethod
    def _city(cls, value):
        return None if value is None else v.normalize_free_text(value, "city", 1, 100)

    @field_validator("state", mode="before", check_fields=False)
    @classmethod
    def _state(cls, value):
        return None if value is None else v.normalize_state(value)

    @field_validator("zip_code", mode="before", check_fields=False)
    @classmethod
    def _zip(cls, value):
        return None if value is None else v.normalize_zip(value)

    @field_validator("email", mode="before", check_fields=False)
    @classmethod
    def _email(cls, value):
        value = _blank_to_none(value)
        return None if value is None else v.clean_text(value).lower()

    @field_validator("insurance_provider", mode="before", check_fields=False)
    @classmethod
    def _ins(cls, value):
        value = _blank_to_none(value)
        return None if value is None else v.normalize_free_text(value, "insurance provider", 1, 100)

    @field_validator("insurance_member_id", mode="before", check_fields=False)
    @classmethod
    def _member(cls, value):
        value = _blank_to_none(value)
        return None if value is None else v.normalize_member_id(value)

    @field_validator("preferred_language", mode="before", check_fields=False)
    @classmethod
    def _lang(cls, value):
        value = _blank_to_none(value)
        return None if value is None else v.normalize_free_text(value, "preferred language", 2, 50).title()

    @field_validator("emergency_contact_name", mode="before", check_fields=False)
    @classmethod
    def _ec_name(cls, value):
        value = _blank_to_none(value)
        return None if value is None else v.normalize_name(value, "emergency contact name", 100)


class PatientCreate(_PatientFields):
    first_name: str
    last_name: str
    date_of_birth: date
    sex: str
    phone_number: str
    email: Optional[EmailStr] = None
    address_line_1: str
    address_line_2: Optional[str] = None
    city: str
    state: str
    zip_code: str
    insurance_provider: Optional[str] = None
    insurance_member_id: Optional[str] = None
    preferred_language: Optional[str] = "English"
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None

    @model_validator(mode="after")
    def _default_language(self):
        if not self.preferred_language:
            self.preferred_language = "English"
        return self


class PatientUpdate(_PatientFields):
    """Partial update: every field optional, but required fields can't be nulled."""

    first_name: Optional[str] = None
    last_name: Optional[str] = None
    date_of_birth: Optional[date] = None
    sex: Optional[str] = None
    phone_number: Optional[str] = None
    email: Optional[EmailStr] = None
    address_line_1: Optional[str] = None
    address_line_2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    insurance_provider: Optional[str] = None
    insurance_member_id: Optional[str] = None
    preferred_language: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None

    @model_validator(mode="after")
    def _no_null_required(self):
        nulled = [f for f in REQUIRED_FIELDS if f in self.model_fields_set and getattr(self, f) is None]
        if nulled:
            raise ValueError(f"required field(s) cannot be empty: {', '.join(nulled)}")
        if not self.model_fields_set:
            raise ValueError("request body must contain at least one field to update")
        return self


class PatientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    patient_id: UUID
    first_name: str
    last_name: str
    date_of_birth: date
    sex: str
    phone_number: str
    email: Optional[str] = None
    address_line_1: str
    address_line_2: Optional[str] = None
    city: str
    state: str
    zip_code: str
    insurance_provider: Optional[str] = None
    insurance_member_id: Optional[str] = None
    preferred_language: str
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    @field_serializer("date_of_birth")
    def _ser_dob(self, value: date) -> str:
        return v.format_dob(value)

    @field_serializer("created_at", "updated_at")
    def _ser_ts(self, value: datetime) -> str:
        from app.models import iso_utc
        return iso_utc(value)
