from datetime import date, timedelta

import pytest

from app import validators as v


@pytest.mark.parametrize("raw,expected", [
    ("(512) 555-0143", "5125550143"),
    ("+1 512 555 0143", "5125550143"),
    ("512.555.0143", "5125550143"),
])
def test_phone_ok(raw, expected):
    assert v.normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", ["555", "555-0143", "012-555-0143", "512-055-0143", "1234567890123"])
def test_phone_bad(raw):
    with pytest.raises(ValueError):
        v.normalize_phone(raw)


def test_dob_formats_and_future():
    assert v.parse_date_of_birth("03/05/1990") == date(1990, 3, 5)
    assert v.parse_date_of_birth("1990-03-05") == date(1990, 3, 5)
    with pytest.raises(ValueError, match="future"):
        v.parse_date_of_birth((date.today() + timedelta(days=1)).strftime("%m/%d/%Y"))
    with pytest.raises(ValueError):
        v.parse_date_of_birth("02/30/1990")
    with pytest.raises(ValueError):
        v.parse_date_of_birth("01/01/1850")


@pytest.mark.parametrize("name", ["O'Brien", "Mary-Kate", "De La Cruz", "José", "Zoë"])
def test_names_ok(name):
    assert v.normalize_name(name) == name


@pytest.mark.parametrize("name", ["", "R2D2", "Bob!", "x" * 51, "--"])
def test_names_bad(name):
    with pytest.raises(ValueError):
        v.normalize_name(name)


def test_state_zip_sex():
    assert v.normalize_state("texas") == "TX"
    assert v.normalize_state("ny") == "NY"
    assert v.normalize_state("Washington DC") == "DC"
    with pytest.raises(ValueError):
        v.normalize_state("ZZ")
    assert v.normalize_zip("78701") == "78701"
    assert v.normalize_zip("787011234") == "78701-1234"
    with pytest.raises(ValueError):
        v.normalize_zip("7870")
    assert v.normalize_sex("prefer not to say") == "Decline to Answer"
    with pytest.raises(ValueError):
        v.normalize_sex("unknown")
