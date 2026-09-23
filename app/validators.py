"""Pure validation / normalization helpers shared by the REST API and the voice
agent tools. Each raises ValueError with a message short and plain enough to be
read to a caller ("that phone number has 7 digits; I need 10")."""
from __future__ import annotations

import re
from datetime import date, datetime

SEX_VALUES = ("Male", "Female", "Other", "Decline to Answer")

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "DC": "District of Columbia", "PR": "Puerto Rico", "GU": "Guam",
    "VI": "U.S. Virgin Islands", "AS": "American Samoa", "MP": "Northern Mariana Islands",
}
US_STATE_CODES = frozenset(US_STATES)
_STATE_BY_NAME = {name.lower(): code for code, name in US_STATES.items()}
_STATE_BY_NAME.update({"washington dc": "DC", "washington d.c.": "DC", "virgin islands": "VI"})

# Letters (any script, so "José" / "Zoë" work), joined by single hyphens,
# apostrophes or spaces ("Mary-Kate", "O'Brien", "De La Cruz").
_NAME_RE = re.compile(r"^[^\W\d_]+(?:[-' ][^\W\d_]+)*$")
_ZIP_RE = re.compile(r"^\d{5}(?:-\d{4})?$")
_MEMBER_ID_RE = re.compile(r"^[A-Za-z0-9]{3,30}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

SEX_ALIASES = {
    "male": "Male", "m": "Male", "man": "Male", "masculino": "Male", "hombre": "Male",
    "female": "Female", "f": "Female", "woman": "Female", "femenino": "Female", "mujer": "Female",
    "other": "Other", "otro": "Other", "non-binary": "Other", "nonbinary": "Other",
    "decline to answer": "Decline to Answer", "decline": "Decline to Answer",
    "prefer not to say": "Decline to Answer", "prefer not to answer": "Decline to Answer",
}


def clean_text(value: str) -> str:
    """Basic sanitization: strip control characters, trim, collapse whitespace."""
    value = _CONTROL_RE.sub("", value)
    return re.sub(r"\s+", " ", value).strip()


def normalize_name(value: str, label: str = "name", max_len: int = 50) -> str:
    value = clean_text(value).replace("’", "'")  # curly apostrophe from STT
    if not value:
        raise ValueError(f"{label} is required")
    if len(value) > max_len:
        raise ValueError(f"{label} must be {max_len} characters or fewer")
    if not _NAME_RE.match(value):
        raise ValueError(f"{label} can only contain letters, hyphens, apostrophes and spaces")
    return value


def normalize_phone(value: str, label: str = "phone number") -> str:
    """Return a bare 10-digit U.S. (NANP) number."""
    digits = re.sub(r"\D", "", str(value))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        raise ValueError(f"{label} has {len(digits)} digits; a U.S. number needs 10 digits including area code")
    if digits[0] in "01":
        raise ValueError(f"{label} has an invalid area code (it can't start with 0 or 1)")
    if digits[3] in "01":
        raise ValueError(f"{label} is not valid (the 4th digit can't be 0 or 1)")
    return digits


def parse_date_of_birth(value: str | date) -> date:
    """Accept MM/DD/YYYY (the spec format) and ISO YYYY-MM-DD."""
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        dob = value
    else:
        text = clean_text(str(value))
        dob = None
        for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
            try:
                dob = datetime.strptime(text, fmt).date()
                break
            except ValueError:
                continue
        if dob is None:
            raise ValueError("date of birth must be a real date in MM/DD/YYYY format")
    today = date.today()
    if dob > today:
        raise ValueError("date of birth can't be in the future")
    if dob.year < 1900:
        raise ValueError("date of birth must be after 1900")
    return dob


def format_dob(dob: date) -> str:
    return dob.strftime("%m/%d/%Y")


def normalize_sex(value: str) -> str:
    key = clean_text(str(value)).lower()
    if key in SEX_ALIASES:
        return SEX_ALIASES[key]
    for canonical in SEX_VALUES:
        if key == canonical.lower():
            return canonical
    raise ValueError("sex must be one of: Male, Female, Other, Decline to Answer")


def normalize_state(value: str) -> str:
    text = clean_text(str(value))
    upper = text.upper().replace(".", "")
    if upper in US_STATE_CODES:
        return upper
    code = _STATE_BY_NAME.get(text.lower())
    if code:
        return code
    raise ValueError(f"'{text}' is not a valid U.S. state")


def normalize_zip(value: str) -> str:
    text = clean_text(str(value)).replace(" ", "")
    digits = re.sub(r"\D", "", text)
    if len(digits) == 9 and _ZIP_RE.match(text) is None:
        text = f"{digits[:5]}-{digits[5:]}"
    if not _ZIP_RE.match(text):
        raise ValueError("ZIP code must be 5 digits, or ZIP+4 like 12345-6789")
    return text


def normalize_member_id(value: str) -> str:
    text = re.sub(r"[\s-]", "", clean_text(str(value))).upper()
    if not _MEMBER_ID_RE.match(text):
        raise ValueError("insurance member ID must be 3 to 30 letters or numbers")
    return text


def normalize_free_text(value: str, label: str, min_len: int = 1, max_len: int = 100) -> str:
    text = clean_text(str(value))
    if len(text) < min_len:
        raise ValueError(f"{label} is required")
    if len(text) > max_len:
        raise ValueError(f"{label} must be {max_len} characters or fewer")
    return text
