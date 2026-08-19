"""Pydantic schemas enforcing the challenge's validation rules server-side.

The voice agent also validates conversationally, but per the spec the API
must never rely on that: everything is re-checked here.
"""
import re
from datetime import date, datetime
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    EmailStr,
    field_serializer,
)

# 50 states + DC + territories with USPS abbreviations.
US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC", "PR", "VI", "GU", "AS", "MP",
}

SEX_VALUES = ("Male", "Female", "Other", "Decline to Answer")

# Letters plus hyphens/apostrophes per spec; internal spaces allowed for
# multi-part names ("Mary Ann", "De La Cruz").
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z' -]*$")
ZIP_RE = re.compile(r"^\d{5}(-\d{4})?$")


def _validate_name(v: Any) -> str:
    if not isinstance(v, str):
        raise ValueError("must be a string")
    v = v.strip()
    if not (1 <= len(v) <= 50):
        raise ValueError("must be 1-50 characters")
    if not NAME_RE.match(v):
        raise ValueError("may only contain letters, hyphens, and apostrophes")
    return v


def _validate_dob(v: Any) -> date:
    """Accept MM/DD/YYYY (spec's spoken format) or ISO YYYY-MM-DD."""
    if isinstance(v, date) and not isinstance(v, datetime):
        parsed = v
    elif isinstance(v, str):
        v = v.strip()
        parsed = None
        for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(v, fmt).date()
                break
            except ValueError:
                continue
        if parsed is None:
            raise ValueError("must be a valid date in MM/DD/YYYY format")
    else:
        raise ValueError("must be a date string in MM/DD/YYYY format")
    if parsed > date.today():
        raise ValueError("cannot be in the future")
    return parsed


def _validate_sex(v: Any) -> str:
    if not isinstance(v, str):
        raise ValueError(f"must be one of: {', '.join(SEX_VALUES)}")
    normalized = v.strip().lower()
    aliases = {
        "m": "Male",
        "f": "Female",
        "decline": "Decline to Answer",
        "prefer not to say": "Decline to Answer",
        "decline to answer": "Decline to Answer",
    }
    for canonical in SEX_VALUES:
        if normalized == canonical.lower():
            return canonical
    if normalized in aliases:
        return aliases[normalized]
    raise ValueError(f"must be one of: {', '.join(SEX_VALUES)}")


def normalize_phone(v: Any) -> str:
    """Normalize to exactly 10 digits; tolerate +1, punctuation, spaces."""
    if not isinstance(v, str):
        v = str(v)
    digits = re.sub(r"\D", "", v)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        raise ValueError("must be a valid 10-digit U.S. phone number")
    if digits[0] in "01":
        raise ValueError("area code cannot start with 0 or 1")
    return digits


def _validate_city(v: Any) -> str:
    if not isinstance(v, str):
        raise ValueError("must be a string")
    v = v.strip()
    if not (1 <= len(v) <= 100):
        raise ValueError("must be 1-100 characters")
    return v


def _validate_state(v: Any) -> str:
    if not isinstance(v, str):
        raise ValueError("must be a 2-letter U.S. state abbreviation")
    v = v.strip().upper()
    if v not in US_STATES:
        raise ValueError("must be a valid 2-letter U.S. state abbreviation")
    return v


def _validate_zip(v: Any) -> str:
    if not isinstance(v, str):
        v = str(v)
    v = v.strip()
    if not ZIP_RE.match(v):
        raise ValueError("must be a 5-digit ZIP or ZIP+4 (e.g. 12345 or 12345-6789)")
    return v


def _validate_line(v: Any) -> str:
    if not isinstance(v, str):
        raise ValueError("must be a string")
    v = v.strip()
    if not (1 <= len(v) <= 200):
        raise ValueError("must be 1-200 characters")
    return v


def _optional(validator):
    def inner(v: Any):
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        return validator(v)

    return inner


NameStr = Annotated[str, BeforeValidator(_validate_name)]
DobDate = Annotated[date, BeforeValidator(_validate_dob)]
SexStr = Annotated[str, BeforeValidator(_validate_sex)]
PhoneStr = Annotated[str, BeforeValidator(normalize_phone)]
CityStr = Annotated[str, BeforeValidator(_validate_city)]
StateStr = Annotated[str, BeforeValidator(_validate_state)]
ZipStr = Annotated[str, BeforeValidator(_validate_zip)]
AddressStr = Annotated[str, BeforeValidator(_validate_line)]
OptAddressStr = Annotated[str | None, BeforeValidator(_optional(_validate_line))]
OptNameStr = Annotated[str | None, BeforeValidator(_optional(_validate_name))]
OptPhoneStr = Annotated[str | None, BeforeValidator(_optional(normalize_phone))]


class PatientCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    first_name: NameStr
    last_name: NameStr
    date_of_birth: DobDate
    sex: SexStr
    phone_number: PhoneStr
    email: EmailStr | None = None
    address_line_1: AddressStr
    address_line_2: OptAddressStr = None
    city: CityStr
    state: StateStr
    zip_code: ZipStr
    insurance_provider: str | None = None
    insurance_member_id: str | None = None
    preferred_language: str = "English"
    emergency_contact_name: OptNameStr = None
    emergency_contact_phone: OptPhoneStr = None


class PatientUpdate(BaseModel):
    """PUT with partial semantics: only supplied fields are validated/updated."""

    model_config = ConfigDict(extra="forbid")

    first_name: NameStr | None = None
    last_name: NameStr | None = None
    date_of_birth: DobDate | None = None
    sex: SexStr | None = None
    phone_number: PhoneStr | None = None
    email: EmailStr | None = None
    address_line_1: AddressStr | None = None
    address_line_2: OptAddressStr = None
    city: CityStr | None = None
    state: StateStr | None = None
    zip_code: ZipStr | None = None
    insurance_provider: str | None = None
    insurance_member_id: str | None = None
    preferred_language: str | None = None
    emergency_contact_name: OptNameStr = None
    emergency_contact_phone: OptPhoneStr = None


class PatientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    patient_id: str
    first_name: str
    last_name: str
    date_of_birth: date
    sex: str
    phone_number: str
    email: str | None
    address_line_1: str
    address_line_2: str | None
    city: str
    state: str
    zip_code: str
    insurance_provider: str | None
    insurance_member_id: str | None
    preferred_language: str
    emergency_contact_name: str | None
    emergency_contact_phone: str | None
    created_at: datetime
    updated_at: datetime

    @field_serializer("date_of_birth")
    def serialize_dob(self, v: date) -> str:
        # ISO 8601 on the wire; the voice agent speaks MM/DD/YYYY.
        return v.isoformat()
