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
from app.routes.patients import _dump
from app.schemas import PatientCreate, PatientUpdate, normalize_phone

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


def tool_register_patient(args: dict, db: Session) -> dict:
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


def tool_find_patient_by_phone(args: dict, db: Session) -> dict:
    try:
        phone = normalize_phone(args.get("phone_number", ""))
    except ValueError as e:
        return {"success": False, "errors": [{"field": "phone_number", "message": str(e)}]}
    patient = db.scalars(
        select(Patient)
        .where(Patient.phone_number == phone, Patient.deleted_at.is_(None))
        .order_by(Patient.created_at.desc())
    ).first()
    if patient is None:
        return {"success": True, "found": False}
    return {
        "success": True,
        "found": True,
        "patient_id": patient.patient_id,
        "first_name": patient.first_name,
        "last_name": patient.last_name,
    }


def tool_update_patient(args: dict, db: Session) -> dict:
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


TOOLS = {
    "register_patient": tool_register_patient,
    "find_patient_by_phone": tool_find_patient_by_phone,
    "update_patient": tool_update_patient,
}


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
    results = []
    for call in _extract_tool_calls(body):
        handler = TOOLS.get(call["name"])
        if handler is None:
            result = {"success": False, "errors": [{"field": "*", "message": f"unknown tool {call['name']}"}]}
        else:
            try:
                result = handler(call["arguments"], db)
            except Exception:
                logger.exception("Tool %s failed", call["name"])
                result = {"success": False,
                          "errors": [{"field": "*", "message": "internal error saving record"}]}
        logger.info("Tool %s -> %s", call["name"], result)
        results.append({"toolCallId": call["id"], "result": json.dumps(result)})
    return {"results": results}
