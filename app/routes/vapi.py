"""Webhook endpoint for Vapi tool calls.

The voice agent's tools (register_patient, find_patient_by_phone,
update_patient) all POST here. Each tool routes through the same Pydantic
validation as the public REST API, so nothing the LLM produces is trusted.

Tool results are returned as short JSON strings for the LLM to read. On
validation failure the result names each bad field so the agent can
re-prompt the caller for exactly that field (an explicit requirement).
"""
import json
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Patient
from app.routes.appointments import book as book_appointment
from app.routes.patients import _dump
from app.schemas import PatientCreate, PatientUpdate, normalize_phone
from app.scheduling import CLINIC_TIMEZONE, available_slots, now_local, speak_datetime, to_local

logger = logging.getLogger("app.vapi")
router = APIRouter(prefix="/vapi", tags=["vapi"])


def _validation_errors(exc: ValidationError) -> dict:
    return {
        "success": False,
        "errors": [
            {
                "field": ".".join(str(p) for p in err["loc"]),
                "message": err["msg"].removeprefix("Value error, "),
            }
            for err in exc.errors()
        ],
    }


def tool_register_patient(args: dict, db: Session, caller_number: str | None = None) -> dict:
    # Prefer the caller's real number from the telephony layer over anything
    # transcribed from speech. Digit sequences are the least reliable thing an
    # STT engine produces, and a fabricated phone number silently corrupts the
    # record. An explicitly supplied number still wins if it is valid.
    if caller_number:
        supplied = args.get("phone_number")
        try:
            if supplied:
                normalize_phone(supplied)
            else:
                raise ValueError("absent")
        except ValueError:
            args = {**args, "phone_number": caller_number}
            logger.info("Using caller ID for phone_number instead of %r", supplied)
    try:
        payload = PatientCreate(**args)
    except ValidationError as exc:
        return _validation_errors(exc)
    patient = Patient(**payload.model_dump())
    db.add(patient)
    db.commit()
    db.refresh(patient)
    record = _dump(patient)
    logger.info("Patient registered via voice agent: %s", record)
    return {"success": True, "patient_id": patient.patient_id,
            "message": f"Registered {patient.first_name} {patient.last_name}."}


def tool_find_patient_by_phone(args: dict, db: Session, caller_number: str | None = None) -> dict:
    # Imperative phrasing: models act on tool output far more reliably than on
    # a conditional buried in a long system prompt, and skipping this step
    # silently disables duplicate detection.
    ASK_NOW = (
        "REQUIRED NEXT ACTION: the system does NOT know this caller's phone "
        "number. Before asking anything else, say: \"And what's the best phone "
        "number to reach you?\" Read the number back one digit at a time, then "
        "call find_patient_by_phone again with it. Do NOT skip this step and do "
        "NOT say you already have their number."
    )
    raw = args.get("phone_number") or caller_number
    if not raw:
        return {
            "success": True,
            "found": False,
            "caller_id_available": False,
            "next_step": ASK_NOW,
        }
    try:
        phone = normalize_phone(raw)
    except ValueError as e:
        return {
            "success": True,
            "found": False,
            "caller_id_available": False,
            "next_step": f"That number was not usable ({e}). {ASK_NOW}",
        }
    patient = db.scalars(
        select(Patient)
        .where(Patient.phone_number == phone, Patient.deleted_at.is_(None))
        .order_by(Patient.created_at.desc())
    ).first()
    # Echo the number back so the agent can read it aloud for confirmation
    # rather than asking the caller to recite digits it may mis-transcribe.
    result = {
        "success": True,
        "found": patient is not None,
        "caller_id_available": True,
        "phone_number_used": phone,
        "phone_number_spoken": " ".join(phone),
        "from_caller_id": not args.get("phone_number"),
    }
    if patient is not None:
        result.update(
            patient_id=patient.patient_id,
            first_name=patient.first_name,
            last_name=patient.last_name,
        )
    return result


def tool_update_patient(args: dict, db: Session, caller_number: str | None = None) -> dict:
    patient_id = args.pop("patient_id", None)
    if not patient_id:
        return {"success": False, "errors": [{"field": "patient_id", "message": "is required"}]}
    patient = db.get(Patient, patient_id)
    if patient is None or patient.deleted_at is not None:
        return {"success": False, "errors": [{"field": "patient_id", "message": "no such patient"}]}
    try:
        payload = PatientUpdate(**args)
    except ValidationError as exc:
        return _validation_errors(exc)
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        return {"success": False, "errors": [{"field": "*", "message": "no fields to update"}]}
    for field, value in updates.items():
        setattr(patient, field, value)
    db.commit()
    db.refresh(patient)
    logger.info("Patient updated via voice agent: %s", _dump(patient))
    return {"success": True, "patient_id": patient.patient_id, "message": "Record updated."}


def tool_get_current_time(args: dict, db: Session, caller_number: str | None = None) -> dict:
    """An LLM has no clock. Without this it either refuses or invents a time."""
    local = now_local()
    return {
        "success": True,
        "spoken": speak_datetime(local),
        "time_spoken": local.strftime("%-I:%M %p").replace("AM", "AM").replace("PM", "PM"),
        "date_iso": local.date().isoformat(),
        "timezone": CLINIC_TIMEZONE,
        "clinic_is_open": local.weekday() < 5 and 9 <= local.hour < 17,
        "clinic_hours": "Monday to Friday, 9 AM to 5 PM",
    }


def tool_list_appointment_slots(args: dict, db: Session, caller_number: str | None = None) -> dict:
    slots = available_slots(db, limit=int(args.get("limit") or 4))
    if not slots:
        return {
            "success": True,
            "slots": [],
            "next_step": "No slots are open in the next two weeks. Offer to have "
                         "the front desk call them back.",
        }
    return {
        "success": True,
        "timezone": CLINIC_TIMEZONE,
        "slots": [
            {"slot_id": s.strftime("%Y-%m-%dT%H:%M"), "spoken": speak_datetime(s)}
            for s in slots
        ],
        "next_step": "Offer the caller two or three of these by their `spoken` "
                     "text. To book, pass back the exact `slot_id` string — "
                     "never a time you composed yourself.",
    }


def tool_book_appointment(args: dict, db: Session, caller_number: str | None = None) -> dict:
    patient_id = args.get("patient_id")
    slot_id = args.get("slot_id") or args.get("starts_at")
    if not patient_id:
        return {"success": False, "errors": [{"field": "patient_id",
                "message": "register the patient first, then book with their patient_id"}]}
    if not slot_id:
        return {"success": False, "errors": [{"field": "slot_id",
                "message": "pass the exact slot_id from list_appointment_slots"}]}
    try:
        appt = book_appointment(db, patient_id, slot_id, args.get("reason"))
    except ValueError as e:
        return {
            "success": False,
            "errors": [{"field": "slot_id", "message": str(e)}],
            "next_step": "Call list_appointment_slots again and offer a fresh slot.",
        }
    local = to_local(appt.starts_at)
    return {
        "success": True,
        "appointment_id": appt.appointment_id,
        "spoken": speak_datetime(local),
        "message": f"Booked for {speak_datetime(local)}.",
    }


TOOLS = {
    "register_patient": tool_register_patient,
    "find_patient_by_phone": tool_find_patient_by_phone,
    "update_patient": tool_update_patient,
    "get_current_time": tool_get_current_time,
    "list_appointment_slots": tool_list_appointment_slots,
    "book_appointment": tool_book_appointment,
}


def _extract_caller_number(body: dict) -> str | None:
    """The caller's real number from the telephony layer, if usable.

    Vapi puts it at message.call.customer.number for inbound PSTN calls. Web
    calls have no customer number, so this is simply absent there.
    """
    call = (body.get("message") or {}).get("call") or {}
    raw = ((call.get("customer") or {}).get("number")) or call.get("customerNumber")
    if not raw:
        return None
    try:
        return normalize_phone(raw)
    except ValueError:
        # Non-US or withheld caller ID: fall back to asking the caller.
        logger.info("Caller ID %r is not a usable U.S. number; will ask instead", raw)
        return None


def _extract_tool_calls(body: dict) -> list[dict]:
    """Normalize Vapi's tool-call payload variants to [{id, name, arguments}]."""
    message = body.get("message", {})
    calls = []
    for item in message.get("toolCallList") or message.get("toolCalls") or []:
        fn = item.get("function") or {}
        name = item.get("name") or fn.get("name")
        args = item.get("arguments", fn.get("arguments", {}))
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        calls.append({"id": item.get("id"), "name": name, "arguments": args or {}})
    return calls


@router.post("/tools")
async def handle_tool_calls(request: Request, db: Session = Depends(get_db)):
    secret = os.environ.get("VAPI_WEBHOOK_SECRET")
    if secret and request.headers.get("x-vapi-secret") != secret:
        raise HTTPException(
            status_code=401, detail={"code": "unauthorized", "message": "Bad webhook secret."}
        )

    body = await request.json()
    caller_number = _extract_caller_number(body)
    results = []
    for call in _extract_tool_calls(body):
        handler = TOOLS.get(call["name"])
        if handler is None:
            result = {"success": False, "errors": [{"field": "*", "message": f"unknown tool {call['name']}"}]}
        else:
            try:
                result = handler(call["arguments"], db, caller_number)
            except Exception:
                logger.exception("Tool %s failed", call["name"])
                result = {"success": False,
                          "errors": [{"field": "*", "message": "internal error saving record"}]}
        logger.info("Tool %s -> %s", call["name"], result)
        results.append({"toolCallId": call["id"], "result": json.dumps(result)})
    return {"results": results}
