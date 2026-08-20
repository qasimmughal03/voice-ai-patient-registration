"""Clinic clock and appointment slot generation.

Slots are generated from clinic hours rather than stored, so there is no
fixture data to go stale — 'mock data is fine' per the spec. Bookings are
real rows, and an already-booked slot is never offered twice.

Everything the caller hears is in clinic-local time; everything persisted is
naive UTC, matching the rest of the schema.
"""
import os
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Appointment

# The clinic's wall-clock timezone. A voice agent that answers "what time is
# it?" must answer in the caller's clinic's terms, not UTC.
CLINIC_TIMEZONE = os.environ.get("CLINIC_TIMEZONE", "America/New_York")
TZ = ZoneInfo(CLINIC_TIMEZONE)

OPEN_HOUR = 9          # 9:00 am, clinic-local
CLOSE_HOUR = 17        # last slot starts before 5:00 pm
SLOT_MINUTES = 30
LEAD_TIME_HOURS = 2    # no same-hour bookings
SEARCH_DAYS = 14       # how far ahead to look


def now_local() -> datetime:
    return datetime.now(TZ)


def to_utc_naive(local_dt: datetime) -> datetime:
    """Clinic-local aware datetime -> naive UTC, as stored."""
    return local_dt.astimezone(timezone.utc).replace(tzinfo=None)


def to_local(naive_utc: datetime) -> datetime:
    """Naive UTC from the database -> clinic-local aware datetime."""
    return naive_utc.replace(tzinfo=timezone.utc).astimezone(TZ)


def speak_datetime(local_dt: datetime) -> str:
    """A phrasing a TTS engine reads naturally: no leading zeros, no am/pm dots."""
    hour = local_dt.hour % 12 or 12
    minute = f":{local_dt.minute:02d}" if local_dt.minute else ""
    meridiem = "AM" if local_dt.hour < 12 else "PM"
    return (
        f"{local_dt.strftime('%A')}, {local_dt.strftime('%B')} "
        f"{local_dt.day} at {hour}{minute} {meridiem}"
    )


def _slots_for_day(day: date) -> list[datetime]:
    """Every clinic slot on a given day. Weekends are closed."""
    if day.weekday() >= 5:
        return []
    slots, cursor = [], datetime.combine(day, time(OPEN_HOUR), tzinfo=TZ)
    end = datetime.combine(day, time(CLOSE_HOUR), tzinfo=TZ)
    while cursor < end:
        slots.append(cursor)
        cursor += timedelta(minutes=SLOT_MINUTES)
    return slots


def _booked_utc(db: Session) -> set[datetime]:
    rows = db.scalars(
        select(Appointment.starts_at).where(Appointment.cancelled_at.is_(None))
    ).all()
    return set(rows)


def available_slots(db: Session, limit: int = 6) -> list[datetime]:
    """The next open slots, clinic-local, soonest first."""
    earliest = now_local() + timedelta(hours=LEAD_TIME_HOURS)
    taken = _booked_utc(db)
    found: list[datetime] = []
    for offset in range(SEARCH_DAYS):
        for slot in _slots_for_day((earliest + timedelta(days=offset)).date()):
            if slot < earliest or to_utc_naive(slot) in taken:
                continue
            found.append(slot)
            if len(found) >= limit:
                return found
    return found


def parse_requested_slot(text: str) -> datetime | None:
    """Resolve a slot the agent echoes back, e.g. '2026-08-21T14:30'.

    The agent is instructed to pass back the exact `slot_id` it was offered,
    so this deliberately accepts only unambiguous ISO input — never a guess
    at free-form speech like "Tuesday afternoon".
    """
    try:
        parsed = datetime.fromisoformat(text.strip())
    except (ValueError, AttributeError):
        return None
    return parsed.replace(tzinfo=TZ) if parsed.tzinfo is None else parsed.astimezone(TZ)


def slot_is_bookable(db: Session, local_dt: datetime) -> tuple[bool, str]:
    """Validate server-side. The agent may offer a stale or invented slot."""
    if local_dt < now_local() + timedelta(hours=LEAD_TIME_HOURS):
        return False, "that time is too soon or already past"
    if local_dt.weekday() >= 5:
        return False, "the clinic is closed at weekends"
    if not (OPEN_HOUR <= local_dt.hour < CLOSE_HOUR):
        return False, "that is outside clinic hours, which are 9 AM to 5 PM"
    if local_dt.minute % SLOT_MINUTES or local_dt.second:
        return False, "appointments start on the hour or half hour"
    if to_utc_naive(local_dt) in _booked_utc(db):
        return False, "that slot has just been taken"
    return True, ""
