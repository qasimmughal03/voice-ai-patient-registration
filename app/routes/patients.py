import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Patient
from app.schemas import PatientCreate, PatientOut, PatientUpdate, _validate_dob, normalize_phone

logger = logging.getLogger("app.patients")
router = APIRouter(prefix="/patients", tags=["patients"])


def _not_found(patient_id: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": "not_found", "message": f"No patient found with id {patient_id}."},
    )


def _get_active_patient(db: Session, patient_id: str) -> Patient:
    patient = db.get(Patient, patient_id)
    if patient is None or patient.deleted_at is not None:
        raise _not_found(patient_id)
    return patient


def _dump(patient: Patient) -> dict:
    return PatientOut.model_validate(patient).model_dump(mode="json")


@router.get("")
def list_patients(
    db: Session = Depends(get_db),
    last_name: str | None = Query(default=None),
    date_of_birth: str | None = Query(default=None),
    phone_number: str | None = Query(default=None),
):
    stmt = select(Patient).where(Patient.deleted_at.is_(None))
    if last_name:
        stmt = stmt.where(func.lower(Patient.last_name) == last_name.strip().lower())
    if date_of_birth:
        try:
            stmt = stmt.where(Patient.date_of_birth == _validate_dob(date_of_birth))
        except ValueError as e:
            raise HTTPException(
                status_code=400,
                detail={"code": "bad_query", "message": f"date_of_birth {e}"},
            )
    if phone_number:
        try:
            stmt = stmt.where(Patient.phone_number == normalize_phone(phone_number))
        except ValueError as e:
            raise HTTPException(
                status_code=400,
                detail={"code": "bad_query", "message": f"phone_number {e}"},
            )
    patients = db.scalars(stmt.order_by(Patient.created_at)).all()
    return {"data": [_dump(p) for p in patients], "error": None}


@router.get("/{patient_id}")
def get_patient(patient_id: str, db: Session = Depends(get_db)):
    return {"data": _dump(_get_active_patient(db, patient_id)), "error": None}


@router.post("", status_code=201)
def create_patient(payload: PatientCreate, db: Session = Depends(get_db)):
    patient = Patient(**payload.model_dump())
    db.add(patient)
    db.commit()
    db.refresh(patient)
    record = _dump(patient)
    # Observability requirement: final collected payload goes to the log.
    logger.info("Patient registered: %s", record)
    return {"data": record, "error": None}


@router.put("/{patient_id}")
def update_patient(patient_id: str, payload: PatientUpdate, db: Session = Depends(get_db)):
    patient = _get_active_patient(db, patient_id)
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(
            status_code=400,
            detail={"code": "empty_update", "message": "Provide at least one field to update."},
        )
    for field, value in updates.items():
        setattr(patient, field, value)
    db.commit()
    db.refresh(patient)
    record = _dump(patient)
    logger.info("Patient updated: %s", record)
    return {"data": record, "error": None}


@router.delete("/{patient_id}")
def delete_patient(patient_id: str, db: Session = Depends(get_db)):
    patient = _get_active_patient(db, patient_id)
    patient.deleted_at = datetime.now(timezone.utc)
    db.commit()
    logger.info("Patient soft-deleted: %s", patient_id)
    return {"data": {"patient_id": patient_id, "deleted": True}, "error": None}
