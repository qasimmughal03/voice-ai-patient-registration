# Voice AI Patient Registration System

A phone-callable AI intake coordinator that registers new patients through
natural conversation, persists them to a database, and exposes the records
through a REST API.

**Live demo**
- Phone number: **+1 (989) 569-8036** — call this to register
- API base URL: **https://voice-ai-patient-registration-production-683f.up.railway.app**
- Interactive API docs: https://voice-ai-patient-registration-production-683f.up.railway.app/docs

Deployed on Railway (FastAPI service + managed Postgres with a persistent
volume). No credentials are needed to call the API.

---

## Architecture

```
  Caller ──phone──▶ Vapi ──────────▶ GPT-4o (Ava, intake coordinator)
                  (STT + TTS)             │
                                          │ tool calls (HTTPS webhook)
                                          ▼
                              FastAPI  /vapi/tools
                                          │
                                   shared validation
                                   (app/schemas.py)
                                          │
                                          ▼
                              SQLAlchemy ──▶ SQLite / Postgres
                                          ▲
                                          │
  Reviewer ──HTTP──▶ FastAPI  /patients ───┘
```

The key design decision: **the voice agent and the public REST API share one
validation layer and one data layer.** The webhook in `app/routes/vapi.py`
constructs the exact same Pydantic models as `POST /patients`, so a value the
LLM hallucinates is rejected identically to a value curled by hand. Nothing the
model produces is trusted.

| Layer | File | Responsibility |
|---|---|---|
| Telephony + STT/TTS | Vapi (managed) | Audio, turn-taking, barge-in |
| Conversation logic | `prompts/system_prompt.md` | Persona, call flow, re-prompt rules |
| Tool contract | `vapi/tools.json` | Function schemas the LLM calls |
| Webhook adapter | `app/routes/vapi.py` | Vapi payload ⇄ service layer |
| Validation | `app/schemas.py` | All field rules, shared by API and agent |
| REST API | `app/routes/patients.py` | CRUD, query filters, soft delete |
| Persistence | `app/models.py`, `app/database.py` | Schema, session management |

## Tech stack and why

- **Vapi** for telephony. It bundles number provisioning, STT, TTS, barge-in,
  and tool-calling. Building Twilio + Deepgram + ElevenLabs by hand would have
  consumed most of the time budget on plumbing the challenge explicitly says it
  isn't testing.
- **FastAPI + Pydantic.** The field validation table in the spec maps almost
  one-to-one onto Pydantic validators, and the same models serve the REST API,
  the voice webhook, and the auto-generated OpenAPI docs.
- **SQLAlchemy + SQLite by default, Postgres via `DATABASE_URL`.** SQLite keeps
  local setup to zero steps; switching to Postgres in deployment is one
  environment variable, with no code change.
- **GPT-4o** rather than a mini model. Intake requires tracking many fields
  across corrections and out-of-order answers; the cheaper models drop fields
  and mishear spelled-out names more often. Latency was acceptable in testing.

## Setup

```bash
uv sync
uv run python -m app.seed          # 2 demo patients
uv run uvicorn app.main:app --reload --port 8000
```

Expose it publicly (any tunnel or host works):

```bash
ngrok http 8000
```

Then create the Vapi assistant and attach a number:

```bash
export VAPI_API_KEY=...                        # Vapi dashboard → API Keys (private)
export PUBLIC_BASE_URL=https://your-tunnel.ngrok-free.app
export VAPI_WEBHOOK_SECRET=some-random-string  # optional but recommended
uv run python vapi/setup_assistant.py
uv run python vapi/attach_number.py <assistant_id_printed_above>
```

`setup_assistant.py` reads the prompt from `prompts/system_prompt.md` and the
tool schemas from `vapi/tools.json`, so **the prompt is version-controlled, not
trapped in a dashboard.** Re-run it with `VAPI_ASSISTANT_ID` set to push edits.

### Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | No | SQLAlchemy URL. Defaults to `sqlite:///./patients.db`. `postgres://` and `postgresql://` are rewritten to use psycopg 3 automatically |
| `SEED_ON_STARTUP` | No | `true` inserts the 2 demo patients on boot if missing |
| `REQUIRE_POSTGRES` | No | `true` refuses to start on SQLite — set this in deployment so an unset `DATABASE_URL` fails loudly instead of silently using a throwaway database |
| `PORT` | No | Port to bind. Defaults to 8000; set explicitly on Railway so the app and the generated domain agree |
| `VAPI_API_KEY` | Setup only | Vapi private key, used by the setup scripts |
| `PUBLIC_BASE_URL` | Setup only | Public URL of this API, for the webhook |
| `VAPI_WEBHOOK_SECRET` | No | If set, `/vapi/tools` requires a matching `X-Vapi-Secret` header |

No secrets are read at request time by the API itself, and none are committed.

## API

All responses use the envelope `{ "data": ..., "error": ... }`.

| Method | Endpoint | Notes |
|---|---|---|
| `GET` | `/patients` | Filters: `?last_name=`, `?date_of_birth=`, `?phone_number=` |
| `GET` | `/patients/{id}` | 404 if missing or soft-deleted |
| `POST` | `/patients` | 201 with created record |
| `PUT` | `/patients/{id}` | Partial updates; only supplied fields change |
| `DELETE` | `/patients/{id}` | Soft delete — sets `deleted_at`, row is retained |

Status codes: 200, 201, 400 (malformed query param), 404, 422 (field
validation), 500. Validation errors name every offending field:

```json
{ "data": null,
  "error": { "code": "validation_error", "message": "Invalid input.",
    "details": [{ "field": "phone_number",
                  "message": "must be a valid 10-digit U.S. phone number" }] } }
```

Query params are forgiving where callers are not: `?phone_number=+1 (415)
555-0123` and `?phone_number=4155550123` match the same record, and
`?date_of_birth=` accepts both `03/12/1985` and `1985-03-12`.

```bash
curl -s "$API/patients?last_name=doe"
curl -s -X POST $API/patients -H 'Content-Type: application/json' \
  -d '{"first_name":"Jane","last_name":"Doe","date_of_birth":"03/12/1985","sex":"Female",
       "phone_number":"4155550123","address_line_1":"123 Market St","city":"San Francisco",
       "state":"CA","zip_code":"94103"}'
```

## Conversation design

The full prompt is in [`prompts/system_prompt.md`](prompts/system_prompt.md),
commented by section. The decisions that matter:

- **One question per turn.** Multi-field questions ("name and date of birth?")
  reliably produce partial answers on the phone.
- **Spelled letters override the transcript.** The prompt states that when a
  caller spells a name, those letters are the truth — this is what makes the
  "D-A-V-I-S, not D-A-V-I-E-S" correction work, since STT often returns a
  plausible-but-wrong name with high confidence.
- **Duplicate check runs early and silently**, right after the phone number, so
  a returning caller is offered an update before re-reciting their address.
- **Optional fields are offered once, as a group.** Asking about insurance,
  emergency contact, and language individually turns a 2-minute call into 4.
- **Errors re-prompt per field.** The webhook returns `{"success": false,
  "errors": [{"field": ..., "message": ...}]}`, and the prompt instructs the
  agent to re-ask only the named fields rather than restarting.
- **Failures are never faked.** On an internal error the agent tells the caller
  the registration did not save and asks them to call back. Silently losing a
  registration is worse than an honest failure.

## Edge case handling

| Situation | Behavior |
|---|---|
| Invalid date of birth (future / impossible) | Rejected in the prompt, and again by `_validate_dob`; agent re-asks only the DOB |
| Short or long phone number | Normalizer strips punctuation and `+1`; anything not 10 digits is re-prompted |
| Call drops mid-conversation | Nothing is written until confirmation, so no partial records exist. The caller starts fresh; no orphaned rows to clean up |
| Database write fails | Webhook catches the exception, logs the traceback, returns a failure result; agent apologizes honestly and ends the call |
| Caller wants to start over | Prompt discards collected state and restarts from the name |
| Out-of-order answers | Prompt captures whatever is offered and asks only for what's still missing |
| Returning caller | `find_patient_by_phone` offers an update instead of creating a duplicate |
| Malicious API input | Server-side validation independent of the LLM; `extra="forbid"` rejects unknown fields; SQLAlchemy parameterizes all queries |

## Observability

Every registration, update, soft delete, and tool call is logged to stdout,
including the full final payload:

```
INFO app.vapi: Patient registered via voice agent: {'patient_id': '...', 'first_name': 'Jane', ...}
INFO app.vapi: Tool register_patient -> {'success': True, ...}
```

## Tests

```bash
uv run pytest
```

14 tests cover field validation, query filters, partial updates, soft-delete
invisibility, and the three voice tools including the duplicate lookup and the
per-field error path.

## Trade-offs and known limitations

- **SQLite by default, Postgres in deployment.** SQLite keeps local setup to
  zero steps, but on a host with an ephemeral filesystem it silently loses
  every record on redeploy. This actually happened during deployment: the
  service booted fine, reported healthy, and was quietly writing to a
  throwaway file. Because a database that *looks* healthy while discarding
  data is worse than one that refuses to start, `REQUIRE_POSTGRES=true` is set
  in production and the app now fails loudly on the SQLite fallback.
- **No authentication on the REST API.** The spec did not ask for it and adding
  it would have blocked reviewer testing. The webhook does support a shared
  secret. Real deployment needs auth on every endpoint.
- **No HIPAA controls.** Explicitly out of scope per the FAQ. No encryption at
  rest, no audit trail, no access controls. Do not put real patient data here.
- **Duplicate detection keys on phone number only.** Two family members sharing
  a landline would collide; a real system would match on name + DOB + phone.
- **Soft-deleted records are invisible to the API entirely.** There is no
  restore endpoint or `?include_deleted=` flag.
- **`PUT` has PATCH semantics.** The spec asked for partial updates on `PUT`;
  implemented as specified, noted here as a deliberate REST deviation.
- **English only.** Multi-language was a listed bonus; the `preferred_language`
  field is stored but does not switch the agent's language mid-call.

## Next steps

1. Persist call transcripts linked to `patient_id` via Vapi's
   `end-of-call-report` webhook (the assistant already subscribes to it).
2. API-key auth plus per-IP rate limiting on the REST endpoints.
3. Alembic migrations instead of `create_all`.
4. Spanish support: detect language on the first turn and swap voice + prompt.
5. Address verification against USPS to catch mis-transcribed street names.
6. A small dashboard listing registered patients.
