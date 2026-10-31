# Autonomous Word Document Agent

A Python/FastAPI assignment implementation: a natural language request becomes a model-generated execution plan, independently executed writing tasks, and a formatted Microsoft Word document. It uses Groq's hosted API, with `qwen/qwen3.8-27b` as the default model. Use a Groq free-tier account within its quota to complete the assignment without purchasing API credits.

## Setup and run (Windows PowerShell)

Python 3.11+ is required. From the project directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

Create an API key at [Groq Console](https://console.groq.com/keys). Open `.env` and set:

```dotenv
AGENT_BACKEND=groq
GROQ_API_KEY=your_actual_groq_api_key
GROQ_MODEL=qwen/qwen3.8-27b
LLM_TIMEOUT_SECONDS=180
ARTIFACT_DIR=artifacts
```

Keep your key in `.env`, which is ignored by Git. Start the API:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000/docs** to submit requests interactively. Internet access is required; requests and document context are sent to Groq. No model download or local GPU is needed. There are normally 2–9 sequential model calls per request (one plan plus 1–8 tasks). At most one repair is allowed for the plan and each failing task, so a run may use up to 18 model calls, excluding HTTP rate-limit retries. Checks and duplicate removal use no model calls. `LLM_TIMEOUT_SECONDS` applies to each HTTP call. Change `GROQ_MODEL` only to a model supporting strict structured outputs and available to your account. See [Groq structured outputs](https://console.groq.com/docs/structured-outputs) and your [account limits](https://console.groq.com/settings/limits).

The client retries HTTP 429 responses up to twice, waiting at most 60 seconds per retry. A longer provider retry delay fails promptly. An exhausted quota returns HTTP 429, with `retry_after_seconds` and a `Retry-After` header when Groq supplies a numeric wait time. If no wait time is provided, the value is null; check your [account limits](https://console.groq.com/settings/limits) for the reset time. Free-tier limits can affect multi-step runs, so allow several minutes for both examples. Avoid repeatedly restarting a failed run: completed model calls would be repeated. Other provider failures are not retried automatically. Environment variables override `.env`; if a terminal previously selected demo mode, run `$env:AGENT_BACKEND = "groq"` before starting the API.

Output-token ceilings default to `GROQ_PLAN_TOKENS=4096`, `GROQ_SECTION_TOKENS=2048`, and `GROQ_BRIEF_SECTION_TOKENS=1024`. Sections with a maximum of 100 body words or fewer use the brief ceiling. These ceilings limit possible output; they are not a measure of tokens consumed and do not remove Groq's quota limits. The client also sends compact JSON to reduce input overhead. Actual token usage is not currently reported.

On Linux/macOS, use `.venv/bin/python` in place of `.\.venv\Scripts\python.exe`.

## API usage

`POST /agent` accepts the following JSON and returns the Word attachment directly by default. Swagger displays **Download file** in the POST response:

```json
{"request":"Create a six-week project proposal for an employee onboarding portal. Include scope, owners, budget, risks and acceptance criteria."}
```

PowerShell example using the supplied business request:

```powershell
$sample = Get-Content examples/standard.json -Raw | ConvertFrom-Json
$body = @{ request = $sample.request } | ConvertTo-Json
$result = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/agent?download=false' -ContentType application/json -Body $body
$result.plan.tasks | Format-Table id,title,depends_on
Invoke-WebRequest -Uri $result.document.download_url -OutFile proposal.docx
```

JSON mode (`download=false`) includes a meaningful `message`, the full `plan`, `execution` records with generated section content, assumptions and decisions, backend/model identification, and `document` metadata with a download URL. Use JSON mode when inspecting planning details. It is optional: a separate download request is not required for the default POST flow.

### Download directly from POST

Use **`POST /agent`** (or `POST /agent?download=true`) to receive the `.docx` attachment directly. In Swagger, expand POST `/agent`, click **Try it out**, leave `download` as **true**, enter your request, and execute. The response offers **Download file** directly under POST. There is no separate download operation shown in Swagger.

Select `download=false` to inspect the generated plan and execution JSON. Direct-download mode is the default and returns the binary document instead of that JSON, with `X-Agent-Run-Id`, `X-Agent-Task-Count`, `X-Agent-Backend` and `X-Agent-Message` response headers. Error responses remain JSON in either mode. Each new POST starts generation again, so switching modes and resubmitting consumes additional model calls. The GET route remains available for existing download links and scripts, but is hidden from Swagger and unnecessary for a direct POST download.

```powershell
$sample = Get-Content examples/standard.json -Raw | ConvertFrom-Json
$body = @{ request = $sample.request } | ConvertTo-Json
Invoke-WebRequest -Method Post -Uri 'http://127.0.0.1:8000/agent?download=true' -ContentType application/json -Body $body -OutFile proposal.docx
```

`GET /documents/{run_id}` returns the `.docx` attachment. `GET /health` checks API liveness only, not API-key validity or model readiness. Generated files remain in `ARTIFACT_DIR` (default `artifacts/`). Invalid input or unsupported explicit section counts return 422; exhausted rate limits return 429; missing/invalid Groq credentials or provider failures return 503; invalid output or failed compliance repair returns 502. A failed run does not publish a partial document. There is no silent fallback to fabricated content.

## Mandatory engineering improvement: multi-step planning

**What:** The planner produces 1–8 tasks, each with an instruction, acceptance criteria and dependencies, plus a separate final document outline. A task's `section_id` identifies the section it produces, or is null for internal analysis. Every outline section must have exactly one producing task. Tasks are validated for unique IDs and references to earlier tasks, preventing missing dependencies and cycles. Dependencies are used only when needed. The executor calls the model separately for each task and passes the actual cleaned results of its dependencies. Only final sections appear in the document, in outline order. JSON mode exposes the full task record; the document includes an execution appendix only when requested.

**Why:** Business documents often require decisions before writing: a budget depends on scope; an acceptance plan depends on scope, delivery capacity and cost. A single generation can contradict itself across these sections.

**Benefit:** Downstream work uses previously agreed decisions. Ambiguous requests can produce explicit assumptions and scope tradeoffs before dependent sections are drafted. Every section has an observable task behind it. This is the chosen mandatory improvement; schema validation and basic HTTP errors are supporting implementation details.

Acceptance criteria guide generation; they are not independently evaluated by a second model. A completed document has passed structural and supported measurable formatting checks, not factual verification.

## Document compliance and bounded repair

The planner supplies `document_format` with required headings, an optional maximum body word count, table permission, and flags controlling metadata, assumptions, decisions and the execution appendix. Common explicit English constraints are also extracted deterministically from the request: `exactly N sections`, comma-separated headings after that phrase and a colon, `no tables`, and per-section limits such as `under 40 words` or `at most 40 words`. Explicit constraints override optional presentation choices. Other wording is interpreted by the planner and checked against the format it supplies; arbitrary natural-language constraint extraction is not guaranteed.

Brief requests and exact outlines omit optional metadata, assumptions, decisions and appendices from the Word document. Those planning details remain available in the API JSON. A title is still rendered; headings are matched without case differences. Body word counts exclude the section heading and include all paragraphs, bullets and table text. Word counting uses whitespace: `under 40` permits at most 39 body words, while `at most 40` permits 40. Bullet-only sections are supported.

Repeated complete sentences and blocks are removed deterministically, preserving the first occurrence. This handles a paragraph being repeated as a bullet; it does not detect every semantic paraphrase. If a plan or section violates the supported checks, the model gets one repair attempt with the specific errors. Section repairs repeat only that task. The run fails without publishing a document if repair does not succeed. `execution[].repair_attempted` shows which tasks needed repair. The renderer checks the assembled outline again before saving.

Regression input:

```json
{"request":"Create a very brief meeting agenda for a 15-minute weekly team check-in. Use exactly three sections: updates, blockers, and next steps. Keep each section under 40 words. No tables."}
```

Its output must have only those three body section headings, fewer than 40 words per section, no tables and no repeated paragraph/bullet content. Re-run the request after restarting the API: existing downloaded documents are not rewritten.

## Two demonstration inputs

1. **Standard business request:** [examples/standard.json](examples/standard.json) asks for a six-week onboarding portal proposal for Acme Services, including an illustrative INR 150,000 allocation. The fixture demonstrates four tasks: scope → schedule → budget → risks and acceptance.
2. **Complex conflicting request:** [examples/complex.json](examples/complex.json) requests a bilingual, fully accurate chatbot in two weeks for INR 50,000 with no approved knowledge base and no customer data leaving the network. The fixture demonstrates five tasks that explicitly replace the accuracy guarantee, defer Hindi and live CRM integration, preserve the network boundary, allocate 80 engineer-hours, and require approval for the revised scope. These are sample decisions; live-model plans may differ.

Run both against the running server:

```powershell
.\.venv\Scripts\python.exe scripts/demo.py
```

This saves `demo-output/standard.docx`, `complex.docx`, and corresponding response JSON. Inspect `backend` and `model` in the responses to distinguish real inference from fixtures. You can use the same script for Groq and demo mode.

### Fixture mode without a model

For a quick, reproducible smoke test, start the API with:

```powershell
$env:AGENT_BACKEND = "demo"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Then run `scripts/demo.py` in a second terminal. Demo mode accepts only the **exact two supplied requests** and requires no API key. It replays authored sample plans and sections through the real executor and renderer. Responses and documents prominently say that no LLM was called. It verifies the plumbing and sample deliverables; it does **not** demonstrate autonomous model decisions. Use Groq mode and rerun both inputs for the autonomous-agent assignment demonstration.

Switch back by stopping the server, running `$env:AGENT_BACKEND = "groq"`, and restarting. Environment variables override `.env`.

## Architecture

```text
fluid/
|-- app/
|   |-- __init__.py
|   |-- main.py              # API endpoints and application wiring
|   |-- config.py            # Environment settings
|   |-- schemas.py           # Validated data contracts
|   |-- prompts.py           # Planner and writer instructions
|   |-- agent.py             # Task planning and execution
|   |-- compliance.py        # Constraint extraction, cleanup and checks
|   |-- text.py              # Shared text sanitation
|   |-- llm.py               # Groq API adapter
|   |-- document.py          # Word rendering
|   `-- fixture_backend.py   # Authored examples for offline demos
|-- tests/
|   |-- conftest.py          # Shared example loader
|   |-- test_api.py          # Endpoint behavior and integration
|   |-- test_agent.py        # Dependency-aware execution
|   |-- test_schemas.py      # Plan validation
|   |-- test_compliance.py   # Word limits and content cleanup
|   |-- test_llm.py          # Groq requests, responses and retries
|   `-- test_document.py     # Generated Word content and downloads
|-- examples/               # Standard and complex sample requests
|-- scripts/demo.py         # HTTP demonstration client
|-- artifacts/              # Generated Word files (ignored by Git)
|-- demo-output/            # Demonstration downloads (ignored by Git)
|-- .env                    # Private settings (ignored by Git)
|-- .env.example            # Configuration template
|-- .gitignore
|-- requirements.txt
|-- requirements-lock.txt
`-- README.md
```

```text
POST /agent {request}
  → Groq planner → validated Plan with ordered tasks and dependencies
  → task executor → structured results using dependency results; one targeted repair on failure
  → constraint checks → final sections assembled in outline order
  → python-docx renderer → atomic .docx save
  → Word attachment directly (default)
  → OR JSON: message + plan + execution + download URL (download=false)
GET /documents/{run_id} → existing-file retrieval, hidden from Swagger
```

- `app/agent.py`: dependency-aware orchestration of planning and task execution.
- `app/prompts.py`: system instructions for the planner and section writer.
- `app/llm.py`: Groq HTTP adapter with authentication, schema-constrained responses and bounded rate-limit retries.
- `app/schemas.py`: request, plan, section, table and response contracts.
- `app/compliance.py`: explicit constraint extraction, sentence deduplication, word counting and outline validation.
- `app/text.py`: shared XML-safe text sanitation.
- `app/document.py`: styled final sections, tables and optional metadata/appendices, with a validation gate before saving.
- `app/main.py`: endpoints, settings injection and error translation.
- `app/fixture_backend.py`: explicitly labeled fixture backend for repeatable testing.

Groq's [structured outputs](https://console.groq.com/docs/structured-outputs) accept a JSON schema through `response_format`, using strict mode. Pydantic then validates the result, including task dependencies and table dimensions that are not enforced by JSON structure alone. The API uses FastAPI's [FileResponse](https://fastapi.tiangolo.com/advanced/custom-response/#fileresponse) for document downloads.

## Validation

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Tests exercise both requests end to end, reopen actual Word ZIP packages, verify sections and assumptions, check dependency-result propagation, invalid plans/requests and document download boundaries. Agenda regression tests verify actual rendered headings, word counts, no tables and no repeated content; additional tests check internal tasks, independent outline order, targeted repairs and failure without a file. Groq tests verify authorization headers, schema submission, missing keys, safe provider errors, bounded rate-limit retries, malformed/incomplete model responses and the complete Groq flow through a mocked HTTP transport. Tests require no API key or network access. They do not substitute for a live Groq quality evaluation. For the assignment demonstration, run both supplied inputs with Groq, save the JSON and Word files, and review the results; fixtures alone do not demonstrate live model planning.

## Scope and limitations

This is a local assignment service, without authentication, background jobs, conversation memory or external research. Run on localhost. Document URLs have random UUIDs but are not an authorization mechanism; add access controls before shared hosting. Documents persist until you remove them. Requests are independent. The model cannot execute arbitrary shell commands or browse the web; autonomous execution here means selecting and completing document-writing tasks within a bounded workflow.

Review generated text, assumptions and illustrative figures before business use. The service checks supported formatting constraints but does not verify factual claims, recalculate arbitrary model-written budgets, or enforce every possible natural-language requirement. Token usage is not currently reported. A Groq API key is required for real generation; use the free plan and stay within the [published quotas](https://console.groq.com/docs/rate-limits). `requirements.txt` provides compatible version ranges; `requirements-lock.txt` records the environment used for local verification. The existing `httpx` dependency handles Groq calls, so no additional SDK is needed.
