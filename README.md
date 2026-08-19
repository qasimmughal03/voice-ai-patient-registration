# Voice AI Patient Registration System

A phone-callable AI intake coordinator that registers new patients through
natural conversation, persists them to a database, and exposes the records
through a REST API.

**Live demo**
- Phone number: **+1 (989) 569-8036** — call this to register (US callers)
- API base URL: **https://voice-ai-patient-registration-production-683f.up.railway.app**
- Dashboard: **https://voice-ai-patient-registration-production-683f.up.railway.app/dashboard**
- Interactive API docs: https://voice-ai-patient-registration-production-683f.up.railway.app/docs

Deployed on Railway (FastAPI service + managed Postgres with a persistent
volume). No credentials are needed to call the API.

> **Note on the phone number.** It is a Vapi-provided number, and Vapi
> restricts free numbers to US national use, so inbound calls originating
> outside the US are dropped by the carrier before reaching Vapi (confirmed:
> such attempts produce no call record on the Vapi side at all). US-based
> reviewers should reach it normally. Because the developer is not US-based,
> the conversation was validated over Vapi's web-call transport, which
> exercises the identical assistant, prompt, tools, webhook, and database —
> only the PSTN leg differs. Migrating to an imported Twilio number would lift
> the restriction and is the first item under Next Steps.

---

## Architecture

```
  Caller ──phone──▶ Vapi ──────▶ Gemini 3.5 Flash (Ava, intake coordinator)
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
- **Gemini 3.5 Flash** as the LLM. On a phone call, latency is a feature: every
  extra hundred milliseconds is heard as an awkward pause, so a flash-tier
  model beats a pro-tier one here. It also has reliable function calling, which
  matters because the agent must fill a nine-field tool call while the caller
  corrects themselves mid-sentence. The provider and model are configurable
  (`LLM_PROVIDER` / `LLM_MODEL`), so swapping to `openai` / `gpt-4o` is a
  one-line change and a re-run of the setup script.
- **AssemblyAI `universal-3-5-pro` for transcription**, reached by elimination
  rather than by assumption. Deepgram nova-2 mis-heard names ("Qasim" →
  "Kassim"); AssemblyAI's `universal-streaming-multilingual` was worse still
  ("Cosmi", "Nomad Kassen") and finalised turns so slowly that calls died on
  silence timeouts. `universal-3-5-pro` transcribes the same audio correctly.
  It also accepts two things the streaming models do not: a `keytermsPrompt`
  boosting terms the model would otherwise mangle (names, "date of birth",
  "ZIP code"), and a domain `prompt` describing the call. Endpointing silence
  is raised to 960ms so callers pausing to recall an address are not truncated
  mid-sentence — truncation was the upstream cause of the LLM gap-filling
  fragments into wrong values.
- **Vapi-provided voice** (Emma) rather than ElevenLabs, so the demo needs no
  third-party TTS credential to reproduce.

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

Then copy `.env.example` to `.env`, fill in your keys, and create the assistant:

```bash
cp .env.example .env      # then set VAPI_API_KEY, PUBLIC_BASE_URL, GEMINI_API_KEY
uv run python vapi/setup_assistant.py
uv run python vapi/attach_number.py <assistant_id_printed_above>
```

`VAPI_API_KEY` must be the **private** key — the public key returns 401. The
setup script registers `GEMINI_API_KEY` with Vapi as a provider credential, so
the model key is stored server-side by Vapi rather than in this repo.

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
| `GEMINI_API_KEY` | Setup only | Registered with Vapi as a Google provider credential on first run |
| `ASSEMBLYAI_API_KEY` (or `ASSEMBLY_API_KEY`) | Setup only | Registered with Vapi as a transcriber credential |
| `LLM_PROVIDER` / `LLM_MODEL` | No | Default `google` / `gemini-3.5-flash` |
| `VOICE_PROVIDER` / `VOICE_ID` | No | Default `vapi` / `Emma` |
| `TRANSCRIBER_PROVIDER` / `SPEECH_MODEL` | No | Default `assembly-ai` / `universal-3-5-pro` |
| `TRANSCRIBER_LANGUAGE` / `TRANSCRIBER_MODE` | No | Default `en` / `balanced` |

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

Plus `GET /dashboard`, a read-only web UI listing registered patients with a
client-side filter. It is self-contained (no build step, no CDN) and reads the
same `/patients` endpoint reviewers use, so there is no second data path to
drift out of sync.

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

### The main one: conversation flow is enforced by prompt, not by code

Every step of the call — what to ask, in what order, what must be collected
before saving — lives in `prompts/system_prompt.md` and is carried out by the
LLM on trust. Nothing in the system *prevents* the agent from departing from
that flow; the prompt only asks it not to.

This is the system's principal weakness, and it is not hypothetical. Across
test calls the agent variously invented values for fields it never asked about,
called `register_patient` before collecting the ZIP code and without the
required read-back, and skipped the phone-number question entirely (which
silently disables duplicate detection, since the lookup needs a number). In
every one of those cases the correct instruction was already present in the
prompt and was simply not followed.

Two defences exist today and both are real, but neither is complete:

- **Server-side validation** rejects anything malformed, so a fabricated
  three-digit phone number cannot reach the database. Its blind spot is
  well-formed but wrong data: `Butte`, `MT`, and `98901` all pass every
  validator while being entirely invented.
- **Imperative tool responses** carry the critical instructions, because models
  act far more reliably on fresh tool output than on a conditional buried in a
  long prompt. `find_patient_by_phone` returning `REQUIRED NEXT ACTION: ...`
  fixed the skipped phone question that three rounds of prompt wording did not.

The structural fix is to move sequencing out of the prompt — see Next steps.
It was consciously deferred rather than overlooked: the challenge asks for an
agent that feels like a human intake coordinator and explicitly *not* a rigid
IVR menu, and a hard state machine trades directly against that. With the
remaining time better spent on a working end-to-end system, the honest position
is that this one is mitigated, not solved.

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
- **English only, by measurement rather than omission.** Multi-language was a
  listed bonus and AssemblyAI's multilingual mode was tried, but it degraded
  English name recognition enough to make the required path worse — the whole
  point of the exercise is registering patients correctly, so accuracy on the
  required flow beat coverage of an optional one. Switching back is one
  variable (`TRANSCRIBER_LANGUAGE=multi`). The `preferred_language` field is
  stored but does not switch the agent's language mid-call.
- **Speech recognition remains the weakest link.** Names and addresses spoken
  over a phone line are genuinely hard, and no transcriber tested got them
  reliably right first time. The mitigations are conversational rather than
  technical: the agent asks callers to spell unusual names, treats spelled
  letters as authoritative over the transcript, reads everything back before
  saving, and takes the phone number from caller ID rather than from speech.

## Next steps

1. **Externalise the collection checklist — the highest-value change here.**
   Add a `save_field` tool the agent calls as each value is captured, and have
   every tool response return what is still outstanding
   (`"still missing: city, zip_code"`). Server-side state then answers "am I
   done?" instead of the model's recollection of the conversation, which is
   precisely the judgement it has proved unreliable at. Crucially this keeps
   the conversation free-form — the agent still chooses phrasing, handles
   out-of-order answers, and recovers from corrections — so it buys correctness
   without the IVR rigidity that a full state machine would impose. It also
   yields partial records that survive a mid-call disconnect, rather than
   losing everything collected so far.

   The stricter alternative is a server-driven state machine: a
   `get_next_question` tool where the backend owns the field order outright and
   the agent asks whatever it is told. Skipping becomes impossible because the
   model never sees the sequence. The costs are an extra round-trip per field
   and a noticeably more mechanical call, so it is the right design for a
   compliance-critical intake and the wrong one for the conversational quality
   this challenge asks for.

2. Import a Twilio number in place of the free Vapi number, lifting the
   US-only inbound restriction described above.
3. Persist call transcripts linked to `patient_id` via Vapi's
   `end-of-call-report` webhook (the assistant already subscribes to it).
4. API-key auth plus per-IP rate limiting on the REST endpoints.
5. Alembic migrations instead of `create_all`.
6. Spanish support: detect language on the first turn and swap voice + prompt.
7. Address verification against USPS to catch mis-transcribed street names.
