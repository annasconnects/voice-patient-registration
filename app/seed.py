"""Two fictional demo patients, inserted only when the table is empty."""
from __future__ import annotations

import logging

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Patient
from app.schemas import PatientCreate
from app.services import patient_service

logger = logging.getLogger("seed")

SEED_PATIENTS = [
    {
        "first_name": "Jane", "last_name": "Doe", "date_of_birth": "04/12/1985", "sex": "Female",
        "phone_number": "5125550143", "email": "jane.doe@example.com",
        "address_line_1": "742 Evergreen Terrace", "address_line_2": "Apt 3B",
        "city": "Austin", "state": "TX", "zip_code": "78701",
        "insurance_provider": "Blue Cross Blue Shield", "insurance_member_id": "XJH123456789",
        "preferred_language": "English",
        "emergency_contact_name": "John Doe", "emergency_contact_phone": "5125550199",
    },
    {
        "first_name": "Carlos", "last_name": "O'Neil-Ramirez", "date_of_birth": "11/30/1972", "sex": "Male",
        "phone_number": "3055550178",
        "address_line_1": "1200 Brickell Ave", "city": "Miami", "state": "FL", "zip_code": "33131-2204",
        "preferred_language": "Spanish",
    },
]


def seed_if_empty() -> None:
    with SessionLocal() as session:
        if session.scalar(select(func.count()).select_from(Patient)):
            return
        for record in SEED_PATIENTS:
            patient_service.create_patient(session, PatientCreate.model_validate(record))
        logger.info("seeded %d demo patients", len(SEED_PATIENTS))
