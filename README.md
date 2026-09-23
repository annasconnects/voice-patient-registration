# Voice AI Patient Registration

Call a real U.S. phone number, talk to **Ava** (an AI intake coordinator), and get registered as a new patient. Ava collects the standard U.S. demographic fields through normal conversation. She validates them as you go, reads everything back, saves the record to Postgres and says goodbye. Call again from the same number and she recognizes you and offers to update your record instead.

| | |
|---|---|
| **Phone number** | **+1 (708) 523-1027** |
| **API base URL** | https://voice-patient-registration-production-fb5b.up.railway.app |
| **Dashboard** | https://voice-patient-registration-production-fb5b.up.railway.app/dashboard |
| **Interactive API docs** | https://voice-patient-registration-production-fb5b.up.railway.app/docs |
| **Credentials** | None needed. Reads and writes are open for review (optional `API_KEY` exists). |

---

## Contents
1. [Try it: suggested test calls](#1-try-it-suggested-test-calls)
2. [Architecture](#2-architecture)
3. [Tech stack and why](#3-tech-stack-and-why)
4. [Repository layout](#4-repository-layout)
5. [Data model](#5-data-model)
6. [REST API](#6-rest-api)
7. [Voice agent design and prompt engineering](#7-voice-agent-design-and-prompt-engineering)
8. [Edge cases and resilience](#8-edge-cases-and-resilience)
9. [Setup: go-live checklist](#9-setup-go-live-checklist)
10. [Local development and tests](#10-local-development-and-tests)
11. [Environment variables](#11-environment-variables)
12. [Observability and security](#12-observability-and-security)
13. [Known limitations and trade-offs](#13-known-limitations-and-trade-offs)
14. [Next steps](#14-next-steps)

---

## 1. Try it: suggested test calls

| Scenario | What to say | Expected |
|---|---|---|
| Happy path | Give your details when asked | Ava confirms everything, saves it, says "You're all set, <name>", offers an appointment, hangs up |
| Out of order | "Hi, I'm John Smith, born March 4th 1988, I live at 12 Oak St in Austin, Texas" | Ava keeps all of it and only asks for what's missing |
| Correction | During the read-back: "Actually my last name is spelled D-A-V-I-S, not D-A-V-I-E-S" | Updates only that field and re-confirms it |
| Invalid date of birth | "January 1st, 2090" | "That date would be in the future…" and she asks again for the date only |
| Invalid phone | "555-1234" | She says it's only 7 digits and asks for the full number with area code |
| Returning caller | Give phone **512-555-0143** (seeded Jane Doe) | "It looks like we already have a record for Jane Doe. Would you like to update your information instead?" |
| Start over | "Can we start over?" | She confirms, clears everything and starts again from your name |
| Spanish | "Hablo español" | Switches to Spanish and stores `preferred_language = Spanish` |
| Hang up mid-call | Hang up halfway | Nothing partial is saved. The call's transcript is still logged (`GET /calls`) |
| Emergency | "I'm having chest pain" | She tells you to hang up and call 911 |

Then check the result: `GET /patients?last_name=<yours>` or open `/dashboard`. Each patient shows the linked call transcript and summary.

---

## 2. Architecture

```mermaid
flowchart LR
    Caller(("📞 Caller")) -- PSTN --> Vapi
    subgraph Vapi["Vapi (managed voice platform)"]
      direction TB
      STT["Deepgram nova-3<br/>speech → text"] --> LLM["GPT-4o<br/>+ system prompt + tools"] --> TTS["ElevenLabs turbo v2.5<br/>text → speech"]
    end
    LLM -- "tool-calls webhook<br/>(x-vapi-secret)" --> WH
    Vapi -- "end-of-call-report<br/>(transcript, summary)" --> WH
    subgraph API["FastAPI service (Railway)"]
      direction TB
      WH["/vapi/webhook<br/>routers/vapi.py"] --> VT["services/voice_tools.py"]
      REST["/patients REST<br/>routers/patients.py"] --> PS
      VT --> PS["services/patient_service.py"]
      PS --> VAL["schemas.py + validators.py<br/>(one rule set)"]
      DASH["/dashboard"] --> REST
    end
    PS --> DB[("PostgreSQL<br/>patients · call_logs · appointments")]
```

**Separation of concerns**

| Layer | Owns | Code |
|---|---|---|
| Telephony, STT, TTS, turn-taking | Vapi (configured in code, not by clicking) | `scripts/setup_vapi.py`, `agent/` |
| Conversation logic | The LLM with a documented system prompt and 6 function tools | `agent/system_prompt.md`, `agent/tools.json` |
| Telephony adapter | Translating Vapi's webhook format into service calls. No business logic here | `app/routers/vapi.py` |
| Agent tools | Short, LLM-friendly results (`status` + `next_step`) | `app/services/voice_tools.py` |
| Business rules and data | Validation, CRUD, soft delete, retry | `app/schemas.py`, `app/validators.py`, `app/services/patient_service.py` |
| HTTP API | REST endpoints, response envelope, status codes | `app/routers/patients.py`, `app/main.py` |

The voice agent and the REST API call **the same service layer**. The agent calls it directly, the spec allows "the same service layer", and it saves an HTTP hop on the latency-sensitive path. Validation is enforced once, server-side, for both.

**Call sequence (happy path)**

1. The caller dials the number. Vapi answers with `firstMessage` and streams speech in and out.
2. The LLM collects the name and date of birth, then calls `validate_fields({date_of_birth})`. An invalid date is caught immediately.
3. It collects the phone number and calls `lookup_patient_by_phone`. If a record exists, it offers an update instead.
4. It collects the address and calls `validate_fields({address...})`, which also normalizes, e.g. "Texas" → `TX`.
5. It offers the optional fields, reads everything back, and applies corrections until the caller says yes.
6. It calls `register_patient({...all fields})`. The server validates again, inserts, logs the payload and links `call_id → patient_id`.
7. It says "You're all set, Maria.", optionally runs `get_available_appointments` then `book_appointment`, and calls `endCall`.
8. Vapi posts an `end-of-call-report`, and the transcript and summary are stored in `call_logs`.

---

## 3. Tech stack and why

| Choice | Why |
|---|---|
| **Vapi** | Real phone number, STT, TTS, barge-in and endpointing out of the box. The 3-hour budget goes into prompt, tools and backend instead of audio plumbing. It supports function tools with webhooks and end-of-call transcripts. |
| **GPT-4o** (temp 0.3) | Reliable function calling and good handling of corrections and out-of-order input, at voice-acceptable latency. The model is set by env var (`LLM_MODEL`) and can be switched to `gpt-4o-mini` for speed. |
| **Deepgram nova-3, `language: multi`** | Accurate phone-audio transcription, and handles English/Spanish code-switching for the language bonus. |
| **ElevenLabs `eleven_turbo_v2_5`** | Natural voice, low latency, multilingual. |
| **Python + FastAPI** | Pydantic gives declarative, reusable validation. OpenAPI docs come for free at `/docs`. |
| **PostgreSQL + SQLAlchemy 2.0** | Real persistence with typed columns and CHECK constraints. Free-tier SQLite on a PaaS lives on an ephemeral disk, which would fail the "second call" requirement. SQLite is still used for local dev and tests; the same schema runs on both. |
| **Railway** | Git-push deploys, Postgres add-on, no sleeping on the paid/trial tier (a cold start during a tool call would mean dead air). |

---

## 4. Repository layout

```
app/
  main.py                   FastAPI app, error envelope handlers, /health, /dashboard
  config.py                 settings from env vars
  db.py                     engine (pool_pre_ping), sessions, create_all
  models.py                 Patient, CallLog, Appointment + CHECK constraints
  schemas.py                Pydantic create/update/output models (validation lives here)
  validators.py             pure normalizers: phone, DOB, state, ZIP, names, ...
  responses.py              {data, error} envelope + ApiError
  seed.py                   2 fictional demo patients
  routers/patients.py       REST CRUD
  routers/calls.py          call transcripts, appointments (read-only)
  routers/vapi.py           Vapi webhook adapter
  services/patient_service.py      CRUD + retry-once on dropped DB connections
  services/voice_tools.py          the 6 agent tools + end-of-call handling
  services/appointment_service.py  mock availability + real bookings
  static/dashboard.html     dashboard UI
agent/
  system_prompt.md          the commented system prompt (comments stripped on upload)
  tools.json                function tool schemas
  vapi_assistant.json       rendered assistant config (generated; secret redacted)
scripts/
  setup_vapi.py             creates/updates the Vapi assistant, attaches the phone number
  simulate_call.py          text-mode conversation with the same prompt + tools (no phone)
tests/                      49 pytest tests (API, validators, webhook, failure paths)
Dockerfile, railway.json, render.yaml, Procfile, .env.example
```

---

## 5. Data model

`patients` (UUID primary key). Every field in the spec, plus `deleted_at` for soft delete.

| Field | DB type | Rules (enforced in Pydantic **and**, where portable, a DB CHECK) |
|---|---|---|
| `patient_id` | `uuid` PK | auto |
| `first_name`, `last_name` | `varchar(50)` NOT NULL | 1–50 chars; letters (any script, so José works), hyphens, apostrophes, spaces* |
| `date_of_birth` | `date` NOT NULL | real date, not in the future, ≥ 1900. Accepts `MM/DD/YYYY` (or ISO), returns `MM/DD/YYYY` |
| `sex` | `varchar(20)` + CHECK IN | `Male`, `Female`, `Other`, `Decline to Answer` (aliases like "prefer not to say" are mapped) |
| `phone_number` | `varchar(10)` + CHECK len=10 | valid NANP 10-digit number; stored as digits only |
| `email` | `varchar(254)` | valid format, lowercased |
| `address_line_1` | `varchar(200)` NOT NULL | required |
| `address_line_2` | `varchar(100)` | optional |
| `city` | `varchar(100)` + CHECK | 1–100 chars |
| `state` | `varchar(2)` + CHECK IN (50 states + DC + territories) | full names are converted ("texas" → `TX`) |
| `zip_code` | `varchar(10)` + CHECK len IN (5,10) | `12345` or `12345-6789` (`123456789` is reformatted) |
| `insurance_provider` | `varchar(100)` | optional |
| `insurance_member_id` | `varchar(50)` | alphanumeric, 3–30 chars, uppercased |
| `preferred_language` | `varchar(50)` NOT NULL | defaults to `English` |
| `emergency_contact_name` / `_phone` | `varchar(100)` / `varchar(10)` | name rules / phone rules |
| `created_at`, `updated_at`, `deleted_at` | `timestamptz` | UTC; `updated_at` is bumped on every modification |

\* Spaces are allowed in addition to the spec's hyphens and apostrophes, so compound surnames like "De La Cruz" are accepted.

Indexes: `last_name`, `phone_number`, `date_of_birth`, `deleted_at`. Phone number is deliberately **not** unique, because family members often share a number. Duplicates are handled conversationally instead.

Supporting tables: `call_logs` (call_id, patient_id FK, outcome, ended_reason, transcript, summary, collected payload) and `appointments` (patient FK, unique `slot_id`).

---

## 6. REST API

All responses use `{"data": ..., "error": null}` or `{"data": null, "error": {"code", "message", "details"}}`.

| Method | Path | Notes | Codes |
|---|---|---|---|
| GET | `/patients` | filters: `last_name` (case-insensitive), `date_of_birth` (MM/DD/YYYY or ISO), `phone_number` (any formatting); `limit`, `offset` | 200, 400 |
| GET | `/patients/{id}` | | 200, 400 (bad UUID), 404 |
| POST | `/patients` | full validation, unknown fields rejected | 201, 400 (bad JSON), 422 |
| PUT | `/patients/{id}` | partial update; required fields can't be set to null | 200, 400, 404, 422 |
| DELETE | `/patients/{id}` | soft delete (sets `deleted_at`); deleted records disappear from reads | 200, 404 |
| GET | `/patients/{id}/calls` | call transcripts linked to the patient | 200, 404 |
| GET | `/patients/{id}/appointments` | booked appointments | 200, 404 |
| GET | `/calls`, `/calls/{call_id}` | all calls, including abandoned ones | 200, 404 |
| GET | `/health` | checks DB connectivity | 200, 503 |

Unexpected errors return a 500 in the same envelope, and the stack trace goes to the logs only.

```bash
BASE=https://voice-patient-registration-production-fb5b.up.railway.app
curl "$BASE/patients?last_name=doe"
curl -X POST $BASE/patients -H 'content-type: application/json' -d '{
  "first_name":"Maria","last_name":"Davis","date_of_birth":"03/05/1990","sex":"Female",
  "phone_number":"(415) 555-2671","address_line_1":"1 Market St","city":"San Francisco",
  "state":"California","zip_code":"94105"}'
# 422 example → details name every bad field:
curl -X POST $BASE/patients -H 'content-type: application/json' -d '{"first_name":"A","last_name":"B",
  "date_of_birth":"01/01/2999","sex":"Male","phone_number":"555","address_line_1":"x","city":"y","state":"ZZ","zip_code":"1"}'
curl -X PUT $BASE/patients/<id> -H 'content-type: application/json' -d '{"last_name":"Davies"}'
curl -X DELETE $BASE/patients/<id>
```

---

## 7. Voice agent design and prompt engineering

The full prompt is in [`agent/system_prompt.md`](agent/system_prompt.md). It includes inline `<!-- design notes -->` that `setup_vapi.py` strips before upload. Key decisions:

- **Written for the ear.** One question at a time. Short turns. No lists or markdown. Varied acknowledgements. Phone numbers in groups, dates in words, spellings letter by letter. That's what keeps it from sounding like an IVR.
- **The flow is a guide, not a script.** The prompt spells out how to handle out-of-order answers, corrections at any time, interruptions (stop and respond) and starting over (confirm, then discard everything).
- **Phone number early.** The returning-caller check runs before the address, so a returning patient isn't asked for data we already have.
- **Validate at checkpoints, not only at the end.** `validate_fields` runs after the date of birth and after the address (plus any optional values), so errors are caught while the topic is fresh. `register_patient` validates everything again server-side.
- **Deterministic tool results.** Every tool returns a `status` (`ok | invalid | found | not_found | saved | save_failed | error | ...`) and often a `next_step`. The prompt tells the model to follow them. Error handling lives in code, and the caller always hears something.
- **Read-back before save.** A mandatory confirmation step. Only changed fields are re-confirmed, and `register_patient`'s tool description repeats "only after explicit confirmation".
- **Optional fields are opt-in.** Email is asked once, lightly. Insurance, emergency contact and language are offered in one sentence, as the spec suggests.
- **Privacy on lookup.** `lookup_patient_by_phone` returns names only, never the date of birth or address, so anyone who knows a phone number can't have a record read out to them.
- **Caller ID.** `{{customer.number}}` is injected, so Ava can offer "the number you're calling from, ending in 0143". `{{date}}` is injected for the "not in the future" check.
- **Idempotent save.** If the model retries `register_patient` within the same call, the server returns the already-created record instead of a duplicate.
- **Low temperature (0.3)** for consistent data capture. `firstMessageInterruptionsEnabled` lets callers talk over the greeting.

Tools: `validate_fields`, `lookup_patient_by_phone`, `register_patient`, `update_patient`, `get_available_appointments`, `book_appointment`, plus Vapi's built-in `endCall`.

---

## 8. Edge cases and resilience

| Situation | Handling |
|---|---|
| Invalid DOB, phone, ZIP, state, email | `validate_fields` returns a plain-language reason per field. The prompt makes Ava re-ask **only that field**. The server re-validates on save and on the REST API. |
| Caller corrects something | The prompt says to use the latest value and re-confirm only the change. The read-back loop repeats until the caller explicitly says yes. |
| Caller wants to start over | Ava confirms, discards all values and restarts from the name. |
| Caller gives info out of order | All volunteered fields are captured. Ava asks only for what's missing. |
| Call drops mid-conversation | Nothing is written until the caller confirms, so there are no half-records. Vapi still sends `end-of-call-report`, and the call is stored in `call_logs` with `outcome = none` and its transcript for follow-up. |
| DB write fails | Tested against a real Postgres outage: the service retries once on a dropped connection (`pool_pre_ping` + retry). If it still fails, the tool returns `save_failed`, and Ava apologizes, offers one retry, and otherwise says clearly that nothing was saved and staff will follow up. Once the DB returns, the retry succeeds with no restart. |
| Tool handler crashes | The webhook catches it and returns `status: error` with a `next_step`. It never returns an HTTP 500 to Vapi, so the caller never hears silence. |
| Webhook slow or unreachable | Vapi's per-tool `request-failed` message makes Ava say "I'm having trouble saving that…" instead of stalling. |
| Returning caller | Detected by phone. Ava offers to update. "That's not me" (shared family phone) leads to a new record. |
| Model double-submits | Idempotency by `call_id`, so no duplicate patient. |
| Spoofed webhook | `x-vapi-secret` shared-secret check (401 otherwise). |
| Silence | Ava asks "Are you still there?" once. Vapi's `silenceTimeoutSeconds: 30` ends dead calls. `maxDurationSeconds: 900` caps runaway calls. |
| Medical emergency or advice | Ava directs the caller to 911. She won't give medical advice. |

---

## 9. Setup: go-live checklist

Budget about 30–40 minutes. Accounts needed: **GitHub**, **Railway**, **Vapi** (the free credit is enough for testing; Vapi supplies the OpenAI, Deepgram and ElevenLabs access, so no extra keys are needed).

### A. Deploy the API (Railway), about 10 minutes
1. Push this repo to GitHub.
2. Railway → **New Project → Deploy from GitHub repo** → pick the repo. It builds from the `Dockerfile` and uses `/health` as the health check.
3. In the project: **+ New → Database → PostgreSQL**.
4. Open the web service → **Variables** and add:
   - `DATABASE_URL` = `${{Postgres.DATABASE_URL}}` (a reference variable)
   - `VAPI_SECRET` = a long random string (`python -c "import secrets;print(secrets.token_urlsafe(32))"`)
5. Web service → **Settings → Networking → Generate Domain**.
6. Check it: open `https://<domain>/health` (expect `"database":"ok"`), then `/patients` (expect the 2 seed patients) and `/dashboard`.

### B. Create the voice agent (Vapi), about 10 minutes
1. Sign up at dashboard.vapi.ai and copy your **Private API Key** (Org → API Keys).
2. **Phone Numbers → Create Phone Number → Free Vapi Number**, and pick a U.S. area code. (If free numbers aren't offered on your account, import a Twilio number instead; the script works the same way.)
3. On your machine, from the repo root:
   ```bash
   pip install httpx
   export VAPI_API_KEY=...            # private key
   export PUBLIC_BASE_URL=https://<domain>
   export VAPI_SECRET=...             # the SAME value you set on Railway
   python scripts/setup_vapi.py       # creates the assistant and attaches it to your number
   ```
   With several numbers, set `VAPI_PHONE_NUMBER_ID`. Re-running the script updates the assistant in place, so edit the prompt and run it again.
   *Manual fallback:* create an assistant in the dashboard and paste the values from `agent/vapi_assistant.json` (the system prompt, tools with server URL and `x-vapi-secret` header, voice and transcriber), then assign it to the number under Phone Numbers → Inbound.
4. Test in the browser first: Vapi dashboard → Assistants → your assistant → **Talk to Assistant**. Then call the number.
5. Watch the Railway logs: you'll see `tool_call`, `registration_complete` and `call_ended` JSON lines.

### C. Before submitting
- Fill in the phone number and URLs at the top of this README.
- Make one full test call and one returning-caller call, and check `/dashboard`.
- Keep the Railway service on a plan that doesn't sleep for the review window.

**Alternative hosting:** `render.yaml` is a Render Blueprint (web service plus Postgres). Avoid Render's free web tier for the live demo, because cold starts would cause dead air on the first tool call.

---

## 10. Local development and tests

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload          # SQLite at ./data/patients.db, seeded
open http://localhost:8000/dashboard   # and /docs

pytest                                 # 49 tests, runs in about 2 s on SQLite
TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/patients pytest   # same suite on Postgres
```

To expose a local server to Vapi, run `ngrok http 8000` and use the ngrok URL as `PUBLIC_BASE_URL`.

**No phone? Use the text simulator.** It runs the same prompt, tools and handlers against your local DB:
```bash
export OPENAI_API_KEY=...
python scripts/simulate_call.py
```

The tests cover validators, every endpoint and status code, soft delete, filters, the API-key guard, persistence across a fresh engine, every webhook tool, webhook payload variants, idempotency, a simulated DB failure, a crashing handler, the appointment double-booking guard and the webhook secret. CI runs them on every push (`.github/workflows/tests.yml`).

---

## 11. Environment variables

| Variable | Where | Required | Purpose |
|---|---|---|---|
| `DATABASE_URL` | API | prod: yes | Postgres URL (`postgres://` and `postgresql://` are both accepted). Unset → local SQLite |
| `VAPI_SECRET` | API + setup script | recommended | Shared secret expected in the `x-vapi-secret` header |
| `API_KEY` | API | no | If set, POST/PUT/DELETE require `X-API-Key`. Leave unset for open review |
| `SEED_DATA` | API | no (default `true`) | Insert 2 fictional patients into an empty DB |
| `LOG_LEVEL` | API | no | Default `INFO` |
| `CLINIC_TIMEZONE` | API | no | Default `America/New_York`, used for mock appointment times |
| `PORT` | API | set by host | Listen port |
| `VAPI_API_KEY` | setup script | yes | Vapi private key (never deployed to the API) |
| `PUBLIC_BASE_URL` | setup script | yes | Public URL of the API |
| `VAPI_PHONE_NUMBER_ID` | setup script | no | Which number to attach |
| `LLM_MODEL`, `VOICE_ID` | setup script | no | Override the model and voice |
| `OPENAI_API_KEY` | simulator | only for the simulator | Text-mode testing |

No secrets are in source. `.env` is git-ignored, and `agent/vapi_assistant.json` is written with the secret redacted.

---

## 12. Observability and security

- **Logs:** JSON lines on stdout (searchable in Railway). Every tool call's arguments and result, `registration_complete` with the **final collected payload**, `update_complete`, `call_ended` (reason, outcome, payload), DB retries and errors with stack traces.
- **Transcripts:** stored per call in `call_logs` and viewable on the dashboard or via `/patients/{id}/calls`.
- **Security:** env-var secrets; webhook shared secret; optional API key for writes; Pydantic rejects unknown fields; control characters stripped and whitespace collapsed; all SQL parameterized by the ORM; the dashboard renders with `textContent` (no HTML injection); DB CHECK constraints as a second line of defence; error responses never include stack traces.

---

## 13. Known limitations and trade-offs

- **No real identity verification.** Anyone who knows a registered phone number can trigger "update your information". Mitigated by revealing names only. A production version would verify the date of birth before allowing updates.
- **Not HIPAA-compliant** (per the brief). The data is fictional. Transcripts contain PHI-like data in plain text, and there's no encryption at rest beyond the host's defaults and no access control on reads.
- **The API is open by default** so reviewers can test it. Set `API_KEY` to lock writes. Reads have no auth.
- **`create_all` instead of migrations.** Fine for one schema version. Use Alembic for the next change.
- **Some checks are app-level only.** Name character sets, email format and phone digits-only are enforced in Pydantic. DB CHECKs are limited to portable SQL (lengths and enums) so SQLite and Postgres share one schema.
- **Duplicate detection is by phone only.** Name plus date-of-birth matching would catch patients who call from a new number.
- **The appointment availability is mock data** (generated slots, real bookings). Timezone handling is basic.
- **Spanish is prompt-driven.** English and Spanish only; the voice and transcriber are multilingual but not tuned per language.
- **STT spelling errors** (letters like M/N, B/D) are mitigated by asking for the last-name spelling and reading it back letter by letter, but not eliminated.
- **Vendor coupling.** The webhook adapter is the only Vapi-specific code, so swapping to Retell or Twilio means rewriting `routers/vapi.py` and `setup_vapi.py`, not the services.

## 14. Next steps

1. Identity verification (DOB match) before updates. Mask the phone number in logs.
2. Alembic migrations. Auth (API key or JWT) on reads. Rate limiting.
3. Fuzzy duplicate detection (name + DOB) and a merge workflow.
4. Address validation (USPS or Smarty) and insurance eligibility checks.
5. An automated conversation eval suite: scripted callers (the simulator plus an LLM caller) covering corrections, invalid data and start-over, scored on field accuracy and turn count.
6. A real scheduling integration, and SMS confirmation after registration.
7. Latency tuning: `gpt-4o-mini` or streaming tool responses, endpointing settings, regional hosting near Vapi.
