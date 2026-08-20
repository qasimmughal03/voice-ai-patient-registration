import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Appointment, Patient
from app.scheduling import (
    CLINIC_TIMEZONE,
    available_slots,
    now_local,
    parse_requested_slot,
    slot_is_bookable,
    speak_datetime,
    to_local,
    to_utc_naive,
)

logger = logging.getLogger("app.appointments")
router = APIRouter(prefix="/appointments", tags=["appointments"])


class AppointmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patient_id: str
    starts_at: str  # ISO clinic-local, e.g. "2026-08-21T14:30"
    reason: str | None = None


def _dump(appt: Appointment) -> dict:
    local = to_local(appt.starts_at)
    return {
        "appointment_id": appt.appointment_id,
        "patient_id": appt.patient_id,
        "starts_at": local.isoformat(),
        "starts_at_utc": appt.starts_at.isoformat(),
        "timezone": CLINIC_TIMEZONE,
        "spoken": speak_datetime(local),
        "reason": appt.reason,
        "created_at": appt.created_at.isoformat(),
    }


def book(db: Session, patient_id: str, starts_at: str, reason: str | None) -> Appointment:
    """Shared by the REST endpoint and the voice agent's tool."""
    patient = db.get(Patient, patient_id)
    if patient is None or patient.deleted_at is not None:
        raise ValueError(f"no patient with id {patient_id}")
    local = parse_requested_slot(starts_at)
    if local is None:
        raise ValueError("starts_at must be an ISO datetime like 2026-08-21T14:30")
    ok, why = slot_is_bookable(db, local)
    if not ok:
        raise ValueError(why)
    appt = Appointment(
        patient_id=patient_id, starts_at=to_utc_naive(local), reason=reason
    )
    db.add(appt)
    db.commit()
    db.refresh(appt)
    logger.info("Appointment booked: %s", _dump(appt))
    return appt


@router.get("/slots")
def list_slots(db: Session = Depends(get_db), limit: int = Query(default=6, ge=1, le=20)):
    slots = available_slots(db, limit=limit)
    return {
        "data": {
            "timezone": CLINIC_TIMEZONE,
            "now": now_local().isoformat(),
            "slots": [
                {"slot_id": s.strftime("%Y-%m-%dT%H:%M"), "spoken": speak_datetime(s)}
                for s in slots
            ],
        },
        "error": None,
    }


@router.get("")
def list_appointments(
    db: Session = Depends(get_db), patient_id: str | None = Query(default=None)
):
    stmt = select(Appointment).where(Appointment.cancelled_at.is_(None))
    if patient_id:
        stmt = stmt.where(Appointment.patient_id == patient_id)
    rows = db.scalars(stmt.order_by(Appointment.starts_at)).all()
    return {"data": [_dump(a) for a in rows], "error": None}


@router.post("", status_code=201)
def create_appointment(payload: AppointmentCreate, db: Session = Depends(get_db)):
    try:
        appt = book(db, payload.patient_id, payload.starts_at, payload.reason)
    except ValueError as e:
        raise HTTPException(
            status_code=422, detail={"code": "unbookable", "message": str(e)}
        )
    return {"data": _dump(appt), "error": None}


@router.delete("/{appointment_id}")
def cancel_appointment(appointment_id: str, db: Session = Depends(get_db)):
    appt = db.get(Appointment, appointment_id)
    if appt is None or appt.cancelled_at is not None:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": f"No appointment {appointment_id}."},
        )
    appt.cancelled_at = datetime.now(timezone.utc)
    db.commit()
    logger.info("Appointment cancelled: %s", appointment_id)
    return {"data": {"appointment_id": appointment_id, "cancelled": True}, "error": None}
