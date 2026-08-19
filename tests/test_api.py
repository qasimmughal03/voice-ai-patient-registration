import json

import pytest
from fastapi.testclient import TestClient

from app.database import Base, engine
from app.main import app

VALID = {
    "first_name": "Ada",
    "last_name": "Lovelace",
    "date_of_birth": "12/10/1815",
    "sex": "Female",
    "phone_number": "415-555-0199",
    "address_line_1": "12 Analytical Way",
    "city": "Berkeley",
    "state": "CA",
    "zip_code": "94704",
}


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_create_normalizes_and_returns_201(client):
    r = client.post("/patients", json=VALID)
    assert r.status_code == 201
    body = r.json()
    assert body["error"] is None
    assert body["data"]["phone_number"] == "4155550199"
    assert body["data"]["date_of_birth"] == "1815-12-10"
    assert body["data"]["preferred_language"] == "English"


@pytest.mark.parametrize(
    "field,value",
    [
        ("phone_number", "123"),
        ("date_of_birth", "01/01/2099"),
        ("state", "XX"),
        ("zip_code", "1234"),
        ("sex", "unknown"),
        ("first_name", "R2D2"),
    ],
)
def test_invalid_field_rejected(client, field, value):
    r = client.post("/patients", json={**VALID, field: value})
    assert r.status_code == 422
    assert any(d["field"] == field for d in r.json()["error"]["details"])


def test_query_filters(client):
    client.post("/patients", json=VALID)
    assert len(client.get("/patients?last_name=lovelace").json()["data"]) == 1
    assert len(client.get("/patients?phone_number=%2B14155550199").json()["data"]) == 1
    assert len(client.get("/patients?date_of_birth=12/10/1815").json()["data"]) == 1
    assert len(client.get("/patients?last_name=nobody").json()["data"]) == 0


def test_partial_update(client):
    pid = client.post("/patients", json=VALID).json()["data"]["patient_id"]
    r = client.put(f"/patients/{pid}", json={"city": "Oakland"})
    assert r.status_code == 200
    assert r.json()["data"]["city"] == "Oakland"
    assert r.json()["data"]["last_name"] == "Lovelace"


def test_soft_delete_hides_from_reads(client):
    pid = client.post("/patients", json=VALID).json()["data"]["patient_id"]
    assert client.delete(f"/patients/{pid}").status_code == 200
    assert client.get(f"/patients/{pid}").status_code == 404
    assert client.get("/patients").json()["data"] == []
    # Record still exists in the table, just flagged.
    assert client.put(f"/patients/{pid}", json={"city": "X"}).status_code == 404


def test_unknown_id_404(client):
    r = client.get("/patients/does-not-exist")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def _tool_call(client, name, args):
    r = client.post(
        "/vapi/tools",
        json={"message": {"toolCallList": [{"id": "t1", "name": name, "arguments": args}]}},
    )
    assert r.status_code == 200
    return json.loads(r.json()["results"][0]["result"])


def test_vapi_register_and_duplicate_lookup(client):
    result = _tool_call(client, "register_patient", VALID)
    assert result["success"] is True

    found = _tool_call(client, "find_patient_by_phone", {"phone_number": "(415) 555-0199"})
    assert found["found"] is True
    assert found["first_name"] == "Ada"

    missing = _tool_call(client, "find_patient_by_phone", {"phone_number": "2025550111"})
    assert missing["found"] is False


def test_vapi_register_reports_bad_fields_for_reprompt(client):
    result = _tool_call(
        client, "register_patient", {**VALID, "phone_number": "12", "date_of_birth": "01/01/2099"}
    )
    assert result["success"] is False
    fields = {e["field"] for e in result["errors"]}
    assert fields == {"phone_number", "date_of_birth"}


def test_vapi_update_existing(client):
    pid = _tool_call(client, "register_patient", VALID)["patient_id"]
    result = _tool_call(client, "update_patient", {"patient_id": pid, "city": "Palo Alto"})
    assert result["success"] is True
    assert client.get(f"/patients/{pid}").json()["data"]["city"] == "Palo Alto"
