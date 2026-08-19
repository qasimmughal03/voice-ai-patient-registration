"""Create or update the Vapi assistant from the local prompt and tool defs.

Usage:
    export VAPI_API_KEY=...           # Vapi private key
    export PUBLIC_BASE_URL=https://your-tunnel-or-host
    uv run python vapi/setup_assistant.py

Re-running with VAPI_ASSISTANT_ID set updates that assistant in place, so the
prompt lives in version control rather than in the Vapi dashboard.
"""
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
API = "https://api.vapi.ai"


def load_dotenv(path: pathlib.Path) -> None:
    """Minimal .env loader so secrets live in a gitignored file, not the shell."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


load_dotenv(ROOT / ".env")

API_KEY = os.environ.get("VAPI_API_KEY")
BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
ASSISTANT_ID = os.environ.get("VAPI_ASSISTANT_ID")
WEBHOOK_SECRET = os.environ.get("VAPI_WEBHOOK_SECRET")
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "google")
LLM_MODEL = os.environ.get("LLM_MODEL", "gemini-2.0-flash")
VOICE_PROVIDER = os.environ.get("VOICE_PROVIDER", "vapi")
VOICE_ID = os.environ.get("VOICE_ID", "Emma")
TRANSCRIBER_PROVIDER = os.environ.get("TRANSCRIBER_PROVIDER", "assembly-ai")
TRANSCRIBER_LANGUAGE = os.environ.get("TRANSCRIBER_LANGUAGE", "multi")
TRANSCRIBER_CONFIG = (
    {"language": TRANSCRIBER_LANGUAGE}
    if TRANSCRIBER_PROVIDER == "assembly-ai"
    else {"model": "nova-2", "language": "en-US"}
)

if not API_KEY or not BASE_URL:
    sys.exit("Set VAPI_API_KEY and PUBLIC_BASE_URL first (see .env.example).")


def request(method: str, path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"{API}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
            # urllib's default User-Agent trips Cloudflare (error 1010).
            "User-Agent": "voice-patient-registration/1.0",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {path} failed [{e.code}]: {e.read().decode()}")


def ensure_provider_credential() -> None:
    """Register the Gemini key with Vapi so the assistant can call Google.

    Vapi stores provider keys server-side; this keeps the key out of the
    assistant config and out of this repo. Safe to re-run — an existing
    credential for the provider is left alone.
    """
    existing = request("GET", "/credential")
    have = {c.get("provider") for c in existing}
    wanted = [
        ("google", os.environ.get("GEMINI_API_KEY"), LLM_PROVIDER == "google"),
        # Accept either spelling; both are in common use.
        ("assembly-ai",
         os.environ.get("ASSEMBLYAI_API_KEY") or os.environ.get("ASSEMBLY_API_KEY"),
         TRANSCRIBER_PROVIDER == "assembly-ai"),
    ]
    for provider, key, needed in wanted:
        if not needed:
            continue
        if provider in have:
            print(f"{provider} credential already present in Vapi.")
        elif key:
            request("POST", "/credential", {"provider": provider, "apiKey": key})
            print(f"Registered {provider} credential with Vapi.")
        else:
            sys.exit(
                f"{provider} is configured but no API key was found. Set the "
                f"matching key in .env (GEMINI_API_KEY / ASSEMBLYAI_API_KEY)."
            )


ensure_provider_credential()

system_prompt = (ROOT / "prompts" / "system_prompt.md").read_text()
tools = json.loads((ROOT / "vapi" / "tools.json").read_text())

server = {"url": f"{BASE_URL}/vapi/tools"}
if WEBHOOK_SECRET:
    server["headers"] = {"X-Vapi-Secret": WEBHOOK_SECRET}

for tool in tools:
    tool["server"] = server
    # Keep the caller company while the webhook round-trips.
    tool["async"] = False

assistant = {
    "name": "Harborview Patient Intake",
    "firstMessage": (
        "Thanks for calling Harborview Family Clinic. This is Ava — I can get you "
        "registered as a new patient. Can I start with your first and last name?"
    ),
    "model": {
        # Override with LLM_PROVIDER / LLM_MODEL in .env. Voice intake wants a
        # low-latency model with reliable function calling; the flash-tier
        # Gemini models and gpt-4o both qualify.
        "provider": LLM_PROVIDER,
        "model": LLM_MODEL,
        "temperature": 0.3,
        "messages": [{"role": "system", "content": system_prompt}],
        "tools": tools,
    },
    # Vapi-provided voice: no third-party TTS credential needed. Override with
    # VOICE_PROVIDER / VOICE_ID to use 11labs, PlayHT, Deepgram, etc.
    "voice": {"provider": VOICE_PROVIDER, "voiceId": VOICE_ID},
    # AssemblyAI Universal-Streaming: markedly better on names, spelled letters,
    # and digit strings than nova-2 in testing, and it reports end-of-turn
    # itself (so no separate smart-endpointing plan). language "multi" covers
    # en/fr/de/it/pt/es; "en" is more accurate if English-only is acceptable.
    "transcriber": {"provider": TRANSCRIBER_PROVIDER, **TRANSCRIBER_CONFIG},
    # Give callers room to think when reciting addresses and numbers.
    "silenceTimeoutSeconds": 30,
    "responseDelaySeconds": 0.3,
    "maxDurationSeconds": 900,
    "endCallFunctionEnabled": True,
    "endCallMessage": "Thanks for calling Harborview. Take care!",
    "serverMessages": ["tool-calls", "end-of-call-report"],
}

if ASSISTANT_ID:
    result = request("PATCH", f"/assistant/{ASSISTANT_ID}", assistant)
    print(f"Updated assistant {result['id']}")
else:
    result = request("POST", "/assistant", assistant)
    print(f"Created assistant {result['id']}")
    print("Save this as VAPI_ASSISTANT_ID in your .env to update it next time.")

numbers = request("GET", "/phone-number")
if numbers:
    for n in numbers:
        print(f"Phone number {n.get('number')} -> assistant {n.get('assistantId')}")
    print("\nAttach your number to this assistant with:")
    print(f"  uv run python vapi/attach_number.py {result['id']}")
else:
    print("\nNo phone numbers on this account yet. Buy one in the Vapi dashboard "
          "(Phone Numbers -> Buy Number), then run vapi/attach_number.py.")
