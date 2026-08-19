"""Point a Vapi phone number at an assistant.

Usage: uv run python vapi/attach_number.py <assistant_id> [phone_number_id]
"""
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

API = "https://api.vapi.ai"


def load_dotenv(path: pathlib.Path) -> None:
    """Minimal .env loader. Duplicated rather than imported from
    setup_assistant, which runs its work at module level."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


load_dotenv(pathlib.Path(__file__).resolve().parent.parent / ".env")
API_KEY = os.environ.get("VAPI_API_KEY")
if not API_KEY:
    sys.exit("Set VAPI_API_KEY first.")
if len(sys.argv) < 2:
    sys.exit(__doc__)

assistant_id = sys.argv[1]


def request(method: str, path: str, payload: dict | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"{API}{path}", data=data, method=method,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": "voice-patient-registration/1.0",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {path} failed [{e.code}]: {e.read().decode()}")


numbers = request("GET", "/phone-number")
if not numbers:
    sys.exit("No phone numbers on this Vapi account. Buy one in the dashboard first.")

number_id = sys.argv[2] if len(sys.argv) > 2 else numbers[0]["id"]
result = request("PATCH", f"/phone-number/{number_id}", {"assistantId": assistant_id})
print(f"{result.get('number')} now answers with assistant {assistant_id}")
