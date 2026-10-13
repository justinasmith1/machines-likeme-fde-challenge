# Contoso Invoice Three-Way Matching

## Overview

Contoso receives supplier invoices. Each invoice is matched against its purchase order (PO) and the goods received notes (GRN) and gets exactly one outcome: **Rejected**, **Held for Review** or **Auto-Pay**. A human (AP analyst / AP manager) reviews held items.

Only invoice data extraction uses an LLM (Azure OpenAI). The rules engine is pure, deterministic Python with unit tests.

Status: reference data loader (phase 1), rules engine (phase 2), invoice extraction (phase 3), matching, persistence, audit trail and business services (phase 4A) and the HTTP API (phase 4B) are implemented. The frontend, real authentication and deployment are not implemented yet.

## Architecture

_Placeholder. To be completed once extraction, engine, persistence and the frontend exist._

```
backend/app/
  domain/      enums and domain models
  reference/   JSON schemas + loader/validator for reference data
  extraction/  document rendering, Azure OpenAI client, cache, mapping to engine input
  matching/    supplier resolution, purchase orders, line mapping, duplicates, approved quantities
  services/    business operations with permissions and audit (no HTTP)
  api/         HTTP endpoints: authenticate, validate, call a service, translate errors
  storage/     uploaded files behind a FileStore interface
  rules/       deterministic rules engine and its parameters
  db/          SQLAlchemy tables and session (SQLite now; portable to PostgreSQL / Azure SQL)
  cli.py       command line entry points
backend/scripts/
  run_extraction_eval.py   manual evaluation against the real model (not part of pytest)
```

## Setup and running locally

Requires Python 3.12.

```bash
cd backend
python -m venv .venv
source .venv/Scripts/activate      # Windows Git Bash; PowerShell: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
cp ../.env.example .env            # then edit; never commit .env (repo root or backend/ both work)

pytest                             # run tests (from backend/)
uvicorn app.main:app --reload      # GET http://localhost:8000/health
```

## Loading reference data

Copy `suppliers.json`, `purchase_orders.json` and `goods_received_notes.json` into `data/reference/`, then from the repo root:

```bash
python -m app.cli load-reference --dir data/reference
```

The loader validates every record, checks referential integrity (PO→supplier, GRN→PO, GRN line→PO line) and either loads everything or fails with an aggregated error report (nothing is written on failure). Loading is idempotent. The SHA-256 of each source file and the load time are stored so the audit trail can state which reference version was used.

## Extraction

Extraction turns an uploaded document into the structured invoice the rules engine consumes. It is the only step that uses a model.

```
file -> render to PNG -> model (classify + transcribe) -> validation -> mapping -> rules engine
```

1. **Render** (`extraction/render.py`). PNG and JPG are decoded and re-encoded as PNG. PDFs are rasterized with PyMuPDF, one image per page. Empty, corrupt, oversized or unsupported files stop here with a clear message.
2. **Model call** (`extraction/client.py`). One request to the Azure OpenAI Responses API with the prompt and the page images, asking for JSON that follows a strict schema.
3. **Validation** (`extraction/schema.py`). The answer is validated with Pydantic. Amounts are decimal strings and dates are ISO; anything else is rejected.
4. **Mapping** (`extraction/mapper.py`). The validated answer becomes the engine input.
5. **Decision** (`services/invoices.py`). Only a document classified as an invoice reaches the rules engine; anything else is recorded as `NOT_AN_INVOICE`.

### What the model decides and what the code decides

| The model | The code |
|---|---|
| Classifies the document: invoice, quotation, credit note or other. | Decides the status. A non-invoice is recorded as `NOT_AN_INVOICE` and never reaches the engine (DL-16). |
| Transcribes what is printed. It is told not to calculate, correct or complete anything. | Checks arithmetic, tolerances, quantities and duplicates in the rules engine. |
| Reports, for invoice number, invoice date, PO references, quantity and unit price, whether the field was `read`, is `illegible`, or is `absent`. | Turns a field that was not read into "no value", which holds the invoice (rules A6, A7, A9) or rejects it when the PO reference is absent (R1). |
| May offer a suggested value for an illegible field. | Keeps the suggestion for the analyst, labelled as a suggestion. It never reaches the engine (DL-18, DL-39). A guess placed in the value itself is demoted to a suggestion. |
| Reports a PO line number only when the document prints one. | Maps invoice lines to PO lines deterministically (matching layer, phase 4, DL-38). |

### Output mode

The client asks for structured outputs (`text.format` with `json_schema`, `strict: true`). If the deployment rejects that parameter, it falls back once to describing the schema in the prompt. In both modes the answer is validated with Pydantic. Which mode the deployment accepts is only known after a real call; the evaluation script prints it.

### Cache

Each extraction is stored in `data/cache/extractions/<key>.json` with the raw model response and the validated result. The key is derived from the file content hash, the prompt version, the schema version and the deployment name, so changing any of them causes a new call (DL-40). The cache holds real invoice data and is git-ignored.

### Rate limit and errors

The deployment allows about 10,000 tokens per minute, roughly three to four extractions.

| Situation | Behaviour |
|---|---|
| HTTP 429 | Wait for `Retry-After` if present, otherwise exponential backoff with jitter. After `EXTRACTION_MAX_RETRIES` retries: `RATE_LIMITED`. |
| HTTP 5xx, timeout, connection error | Backoff and retry, then `MODEL_UNAVAILABLE`. |
| HTTP 400, 401, 403 | No retry: `MODEL_REQUEST_REJECTED`. |
| Answer that is not valid against the schema | One retry, then `INVALID_MODEL_OUTPUT`. |
| Answer cut off by the output token limit | `TRUNCATED_MODEL_OUTPUT`, no retry. |
| Empty, corrupt, oversized, too many pages, unsupported type | `UNREADABLE_DOCUMENT` or `UNSUPPORTED_DOCUMENT_TYPE`; the model is not called. |
| Endpoint or key not configured | `MISSING_CREDENTIALS`; no request is made. |
| Unexpected error while extracting or evaluating | `PROCESSING_ERROR`; the detail goes to the server log only, and the user sees a fixed plain message. |

Any of these leaves the invoice as `EXTRACTION_FAILED` with the error code and a plain-language message, and it can be retried. Failures are not cached.

### Evaluating against the real model

`pytest` never calls the API: every test uses a mock transport, and a guard fails any test that opens a real connection. The real model is exercised by a manual script that compares its output with hand-verified extractions and with the golden set:

```bash
cd backend
python scripts/run_extraction_eval.py                 # all documents, using the cache
python scripts/run_extraction_eval.py --only INV-06   # one document
python scripts/run_extraction_eval.py --no-cache      # force new model calls
```

It waits between real calls to stay within the rate limit, prints a summary and writes the detail to `data/cache/eval_report.json`.

### Evaluation results

The evaluation script was run against the real deployment on the 12 test documents, with prompt version 2:

- **Outcomes:** 12 of 12 matched the golden set, including the quotation classified as not an invoice.
- **Fields:** no differences against the hand-verified extractions, in headers or lines. The fields verified as illegible were reported as illegible.
- **Output mode:** structured outputs (`json_schema`, strict) are accepted by the deployment; the fallback was not needed.
- **Cost:** about 2,500 tokens per document (30,126 tokens for the 12 documents).

An earlier run with prompt version 1 also gave 12 of 12 outcomes, with one field difference: on one document the model returned the supplier tax identifier with its printed label ("Tax ID: ..."). The outcome was still correct, but the supplier is resolved by tax identifier, so the same behaviour on another invoice could have produced a false supplier mismatch (R3).

It was corrected in two places. The code normalizes tax identifiers on both sides before comparing them (DL-43), which is the main defence because it does not depend on the model. The prompt (version 2) also asks for values without their labels, and the difference no longer appears.

## Data model

SQLAlchemy models, SQLite for now. Column types are chosen to port to PostgreSQL or Azure SQL. The schema is created with `create_all`, which creates missing tables but does not alter existing ones: during development, delete the local database file after a schema change. A migration tool (Alembic) is needed once there is data to keep.

| Table | One row per | Notes |
|---|---|---|
| `ref_suppliers`, `ref_purchase_orders`, `ref_po_lines`, `ref_grns`, `ref_grn_lines` | Reference record | Replaced as a whole on each reference load (DL-23). |
| `ref_sources` | Loaded version of a reference file | File name, SHA-256, load time. |
| `invoices` | Uploaded document | File name, content hash, stored path, who uploaded it and when, current status, and the supplier, invoice number, total and currency from the latest evaluation. |
| `extraction_versions` | Extraction of an invoice | Version 1 comes from the model; each human correction adds a version. Holds the validated data, the raw model response, prompt version and model. |
| `evaluations` | Run of the rules engine | Outcome, every reason (code, message, source, decision log reference), the parameter snapshot, the reference data hashes, and how the invoice was matched. Never modified. |
| `human_decisions` | Decision by a person | Decision, previous status, mandatory comment, who decided, their role and the role that was required. A decision replaced by a later one, or by a correction of the data, is marked as superseded and kept. |
| `audit_events` | Thing that happened | Append-only. |
| `rule_parameters` | Version of the parameters | The highest version is in force. |

Invoices refer to suppliers and purchase orders by value, not by foreign key, so reloading reference data neither breaks nor rewrites invoice history. What was true at the time is kept in the evaluation: the reference file hashes and the matching result.

Uploaded files are stored as `data/uploads/<sha256>.<ext>` (git-ignored) behind a `FileStore` interface, so the local folder can be replaced by Azure Blob Storage in deployment (DL-44).

## Matching

`matching/` builds everything the rules engine needs for one invoice, from the database. It is deterministic code; the model does not take part (DL-38).

- **Supplier:** by normalized tax identifier (DL-43); if that finds nothing, by exact normalized legal name; otherwise unresolved, and the engine rejects under R3. A match must be unique.
- **Purchase orders:** every PO number read from the document is looked up. The ones that do not exist are reported to the engine (R2). More than one PO on an invoice always holds it (A10, DL-37).
- **Received quantity:** the sum of every goods received note for the PO line.
- **Quantity already invoiced:** only invoices approved for payment count, that is Auto-Pay or approved by a person (DL-21, DL-36). Held, rejected and undecided invoices reserve nothing, and neither does an Auto-Pay invoice that a reviewer later rejected.
- **Duplicates:** any invoice uploaded earlier with the same supplier and invoice number, whatever its status (DL-25).
- **Line mapping** (DL-15), using the first method that finds exactly one PO line:

| Order | Method | Rule |
|---|---|---|
| 1 | `PRINTED_PO_LINE` | The PO line number printed on the document, if that line exists. |
| 2 | `DESCRIPTION` | Normalized description identical to a PO line description. |
| 3 | `ITEM_CODE` | A PO line item code contained in the invoice line description. |
| 4 | `UNMAPPED` | No single match. The engine holds the invoice (H3). |

The method used for each line is stored with the evaluation and can be shown to the analyst.

## Audit trail

Every change of status writes a row in `audit_events`: who (or `system`), with which role, when, and the details. Examples: `INVOICE_SUBMITTED`, `EXTRACTION_SUCCEEDED`, `EXTRACTION_FAILED`, `CLASSIFIED_NOT_AN_INVOICE`, `INVOICE_EVALUATED`, `EXTRACTION_CORRECTED`, `HELD_AFTER_CORRECTION`, `HUMAN_DECISION_RECORDED`, `HUMAN_DECISION_SUPERSEDED`, `DECISION_DENIED`, `PARAMETERS_UPDATED`.

The table is append-only. The application refuses any update or delete of an audit event, whether made through an object or through a bulk statement, and a test verifies it (DL-52). In deployment the database user should additionally be granted only INSERT and SELECT on this table.

For any invoice, the trail together with `extraction_versions`, `evaluations` and `human_decisions` answers: what was read from the document and by whom, which rules fired, with which parameters and which reference data, who decided what and why.

## Human decisions

Permissions are enforced in the services, not only at the API, and failures raise typed errors (`PermissionDenied`, `NotFound`, `InvalidState`, `ValidationError`) (DL-47).

An AP Analyst or the AP Manager can change the final status of any invoice (DL-48):

| Decision | Applies when | Resulting status |
|---|---|---|
| Approve | The rules engine did not reject the invoice, and it is not already payable. | `APPROVED_FOR_PAYMENT` |
| Override | The rules engine rejected the invoice (DL-13). | `OVERRIDDEN` |
| Reject | Any final status, including Auto-Pay. | `REJECTED_BY_REVIEWER` |
| Request credit note | Any final status, including Auto-Pay. | `CREDIT_NOTE_REQUESTED` |

The status names do not say who decided, because the AP Manager uses them too. Who decided, with which role, and which role was required are kept in `human_decisions`.

- **A comment is mandatory** for every decision, including a second one.
- **Intake users cannot decide.**
- **Not decidable:** an invoice that is still `PENDING_EXTRACTION` or `EXTRACTION_FAILED`, and a document that is not an invoice, which can never be approved for payment (DL-16).
- **A new decision replaces the previous one.** The previous decision stays in `human_decisions`, marked as superseded; nothing is deleted.
- **Approval limit:** approving or overriding an invoice whose total is above the analyst approval limit in force requires the AP Manager, also when there was an earlier decision. An analyst who tries gets a clear permission error, and the attempt is written to the audit trail. Rejecting or requesting a credit note does not release payment and is not limited (DL-49).
- **The engine's evaluation is never modified.** The decision records the previous status next to it.

### Corrections never approve payment

An analyst or the manager can correct the extracted data. That adds an extraction version and evaluates the invoice again with the parameters in force; earlier versions and evaluations stay in the history (DL-12).

Correcting data is not approving payment (DL-46). The new evaluation is stored with the engine's outcome, which may be Auto-Pay, but the invoice stays `HELD_FOR_REVIEW` until a person decides, whether or not anyone had decided before. Its detail shows the reason: "Extracted data was corrected by <user>; a human decision is required before payment." A decision taken on the earlier data is marked as superseded.

The same person may correct and then approve. They are two separate actions, each with its own comment and audit event; a second person is not required.

Status path of a held invoice that is corrected and approved:

```
PENDING_EXTRACTION -> HELD_FOR_REVIEW -> (correction) HELD_FOR_REVIEW -> (approval) APPROVED_FOR_PAYMENT
```

## Parameters

The price tolerance, the analyst approval limit, the rounding tolerance and the list of enabled assumption rules are stored in `rule_parameters`, as versions. Only the AP Manager can change them, with a mandatory comment; a change adds a version and writes an audit event. The first version is seeded from configuration.

A change affects only evaluations made after it. Each evaluation keeps a snapshot of the parameters it used, so an earlier decision can always be explained with the values that applied then (DL-11). Spec rules cannot be switched off; only assumption rules can (DL-27).

## API

The API is a thin layer over the services: it authenticates the caller, validates the request, calls a service and translates errors. Business rules and permissions live in the services. No endpoint changes reference data.

| Method and path | Roles | What it does |
|---|---|---|
| `GET /api/me` | All | Current user and role. |
| `POST /api/invoices` | All | Upload a document (multipart field `file`). Answers `202` with the id and status `PENDING_EXTRACTION`; extraction and evaluation run in the background. |
| `GET /api/invoices` | All | List, newest first. Optional `?status=`. |
| `GET /api/invoices/{id}` | All | Detail. The content depends on the role (see below). |
| `GET /api/invoices/{id}/document` | All | The original file. |
| `GET /api/invoices/{id}/audit` | AP Manager | Audit trail of the invoice (DL-56). |
| `POST /api/invoices/{id}/retry` | AP Analyst, AP Manager | Retry a failed extraction, in the background. |
| `POST /api/invoices/{id}/corrections` | AP Analyst, AP Manager | Correct extracted data: `{"corrections": {...}, "comment": "..."}`. |
| `POST /api/invoices/{id}/decisions` | AP Analyst, AP Manager | Decide: `{"decision": "APPROVE" \| "REJECT" \| "REQUEST_CREDIT_NOTE" \| "OVERRIDE", "comment": "..."}`. |
| `GET /api/review-queue` | AP Analyst, AP Manager | Invoices held for review with no decision in force, oldest first. |
| `GET /api/parameters` | AP Analyst, AP Manager | Parameters in force (DL-57). |
| `PUT /api/parameters` | AP Manager | New parameter version: `{"values": {...}, "comment": "..."}`. |
| `GET /api/parameters/history` | AP Manager | Every parameter version. |
| `GET /health` | Open | Liveness check. |

**What each role sees of an invoice.** Reviewers get the extracted data with the reading status of each field, suggestions labelled as suggestions, the PO line each invoice line was mapped to and the method used, the evaluation in force with every reason (code, message, SPEC or ASSUMPTION, decision log reference), the parameter snapshot, every human decision including superseded ones, and the list of extraction versions. Intake users get only the file name, the upload time, the status and the reasons in plain language: no rule codes, no parameters, no reviewer names or comments, no audit trail (DL-60).

**Errors** are JSON with a stable code and a readable message: `{"error": {"code": "PERMISSION_DENIED", "message": "..."}}`.

| Situation | HTTP | Code |
|---|---|---|
| Not authenticated | 401 | `UNAUTHENTICATED` |
| Role not allowed, or analyst above the approval limit | 403 | `PERMISSION_DENIED` |
| Invoice does not exist | 404 | `NOT_FOUND` |
| Action does not apply to the invoice in its status | 409 | `INVALID_STATE` |
| File larger than `MAX_UPLOAD_MB` | 413 | `FILE_TOO_LARGE` |
| Missing comment, invalid correction, unknown decision or status | 422 | `VALIDATION_ERROR` |
| File type other than PDF, PNG or JPG | 422 | `UNSUPPORTED_DOCUMENT_TYPE` |

CORS allows only the origin in `FRONTEND_ORIGIN` (DL-58).

**Background processing.** The upload answers at once. Extraction and evaluation run afterwards in a FastAPI background task that opens its own database session, because the request's session is closed by then (DL-59). The client polls `GET /api/invoices/{id}` to see the status change.

## Authentication

Authentication sits behind a single dependency, `current_user`, which returns a user name and a role. Endpoints depend only on it.

- **Now (development).** When `APP_ENV=development` is set explicitly, the user and role come from two request headers: `X-Dev-User` and `X-Dev-Role` (`INTAKE`, `AP_ANALYST` or `AP_MANAGER`). This is a development convenience, not security: anyone who can reach the API can claim any role.
- **Any other environment, including when `APP_ENV` is not set.** The headers are ignored and every request is answered with `401`. `APP_ENV` defaults to `production`, so a system with missing configuration is closed, not open.
- **Phase 6.** The dependency is replaced by Microsoft Entra ID (token validation, roles from app roles or groups). Endpoints and services do not change.

## Running the API locally

From `backend/`, with the virtual environment active and a `.env` that contains `APP_ENV=development` (see `.env.example`):

```bash
pip install -e ".[dev]"                                    # after pulling: picks up new dependencies
python -m app.cli load-reference --dir ../data/reference   # once, and after reference data changes
uvicorn app.main:app --reload                              # http://localhost:8000, docs at /docs
```

Starting the app creates any missing tables and seeds the rule parameters. It does not load reference data: that is always the CLI step above.

Try it with the development headers. They work only because `.env` sets `APP_ENV=development`; without that line every request below gets `401`:

```bash
# Upload as an intake user
curl -H "X-Dev-User: ines" -H "X-Dev-Role: INTAKE" -F "file=@../data/test-invoices/INV-05.pdf" http://localhost:8000/api/invoices

# What the intake user sees, and what an analyst sees
curl -H "X-Dev-User: ines" -H "X-Dev-Role: INTAKE" http://localhost:8000/api/invoices/1
curl -H "X-Dev-User: ana" -H "X-Dev-Role: AP_ANALYST" http://localhost:8000/api/invoices/1

# Decide, with a mandatory comment
curl -H "X-Dev-User: ana" -H "X-Dev-Role: AP_ANALYST" -H "Content-Type: application/json" \
  -d '{"decision": "APPROVE", "comment": "Price agreed with purchasing."}' \
  http://localhost:8000/api/invoices/1/decisions

# Audit trail, AP Manager only
curl -H "X-Dev-User: max" -H "X-Dev-Role: AP_MANAGER" http://localhost:8000/api/invoices/1/audit
```

An upload calls the model unless the same file was extracted before with the same prompt, schema and deployment, in which case the answer comes from `data/cache/`.

### Known limitations

- **No schema migrations.** Tables are created with `create_all`, which adds missing tables but never alters existing ones. After pulling a change to the tables, delete the local database file (`backend/contoso.db`) and load the reference data again. Production needs a migration tool such as Alembic.
- **In-process background work.** Background tasks run inside the API process, with no queue. An unexpected error while processing marks the invoice `EXTRACTION_FAILED` (code `PROCESSING_ERROR`) so it can be retried. Only if the process itself stops mid-task does the invoice stay `PENDING_EXTRACTION`, and it is not resumed automatically (DL-59).
- **SQLite.** Fine for one process. Several API processes writing at once need PostgreSQL or Azure SQL.

## Configuration

Read from environment variables / `.env` (see `.env.example`).

| Variable | Default | Notes |
|---|---|---|
| `APP_ENV` | `production` | Set to `development` in a local `.env` to enable the development login headers. Any other value, or no value, disables them (DL-55) |
| `FRONTEND_ORIGIN` | `http://localhost:3000` | The only browser origin allowed to call the API (DL-58) |
| `AZURE_OPENAI_ENDPOINT` | – | Base URL of the v1 API; requests go to `{endpoint}/responses`. Needed only for extraction |
| `AZURE_OPENAI_API_KEY` | – | Secret. Needed only for extraction |
| `AZURE_OPENAI_DEPLOYMENT` | `JustinaSmith-RCS2` | |
| `PRICE_TOLERANCE_PCT` | `0.02` | |
| `ANALYST_APPROVAL_LIMIT` | `5000` | |
| `REFERENCE_DATA_DIR` | `data/reference` | |
| `DATABASE_URL` | `sqlite:///./contoso.db` | See DL-23 |
| `EXTRACTION_MAX_RETRIES` | `3` | Retries after the first attempt, per model call (DL-41) |
| `EXTRACTION_TIMEOUT_SECONDS` | `90` | Per request |
| `EXTRACTION_CACHE_DIR` | `data/cache/extractions` | Relative paths resolve against the repo root (DL-40) |
| `PDF_RENDER_DPI` | `170` | DL-42 |
| `PDF_MAX_PAGES` | `5` | Longer PDFs are rejected (DL-42) |
| `MAX_UPLOAD_MB` | `10` | Larger files are rejected (DL-42) |
| `UPLOADS_DIR` | `data/uploads` | Uploaded documents; relative paths resolve against the repo root (DL-44) |

## Security notes

- Secrets live only in `.env` (git-ignored) or the deployment environment. `.env.example` has placeholders only.
- The API key is a `SecretStr`; it is never printed or logged. It is sent only in the request header, and error text returned by the service is masked before it is stored or shown.
- The model is called only from the server. The browser never receives keys or calls Azure OpenAI directly.
- Real reference data and test invoices are not committed (DL-22).
- Model responses are cached under `data/cache/`, which is git-ignored because it contains real invoice data.
- Uploaded documents are stored under `data/uploads/`, which is git-ignored.
- Tests never reach the network: a test guard fails on any real connection.
- The development login headers work only when `APP_ENV=development` is set explicitly. The default is `production`: with no configuration the API answers 401 to everything, until real authentication is configured (DL-55).
- Intake users never receive rule codes, parameters, reviewer names or comments, or the audit trail (DL-60).

## Deployment

_Placeholder._

## Release process

_Placeholder._

## Decision log

Entries marked closed with source 'Recruitment reply' were confirmed by the client. Entries marked closed without that source are candidate decisions the client left open. Entries marked open are candidate assumptions pending confirmation. Entries closed with source 'challenge brief' follow directly from the written requirements.

| ID | Question or assumption | Adopted answer | Consequence for implementation | Status |
|---|---|---|---|---|
| DL-01 | Inactive supplier: the spec does not mention it in the rejection rules. | Adopted: hold for review (candidate decision; the client explicitly left this open on 12 Oct 2026). Reasoning: spec section 7 reserves rejection for conditions that can never be paid; an inactive status can be stale or reversed, so a human confirms. | Rule A1 (ASSUMPTION, holds), switchable through `enabled_assumption_rules` without a code change. Rejected only if combined with an R rule. | closed |
| DL-02 | Price tolerance limit. | Assumption: inclusive, 2% over the PO unit price, per line. | `invoice_price <= po_price * (1 + tolerance)` per line. | open |
| DL-03 | Invoiced quantity lower than received. | Assumption: not a hold. | No rule triggers on under-invoicing. | open |
| DL-04 | Invoiced quantity above ordered but within received. | Assumption: hold as a precaution. | Hold rule when invoiced > ordered, rule A3 (ASSUMPTION, holds). Disableable through `enabled_assumption_rules` without a code change. | open |
| DL-05 | PO status closed or partially_invoiced. | Assumption: closed rejects; partially_invoiced is evaluated normally. | Rejection rule on closed PO, rule A2 (ASSUMPTION, rejects). Disableable through `enabled_assumption_rules` without a code change. | open |
| DL-06 | Definition of duplicate. | Assumption: same supplier_id + invoice_number. | Uniqueness lookup on that pair. | open |
| DL-07 | Invoice currency differs from PO currency. | Assumption: hold. | Hold rule on currency mismatch, rule A4 (ASSUMPTION, holds). Disableable through `enabled_assumption_rules` without a code change. | open |
| DL-08 | Arithmetic inconsistency in totals. | Assumption: hold. | Hold rule on line total and subtotal/tax/total mismatch beyond the DL-17 tolerance, rule A5 (ASSUMPTION, holds). Disableable through `enabled_assumption_rules` without a code change. | open |
| DL-09 | Several POs on one invoice. | Assumption: if one fails, the whole invoice is rejected. | Evaluate per PO, combine with Rejected winning. | open |
| DL-10 | Line with no determinable PO line. | Assumption: hold (spec rule). | Hold rule on unmapped line. | open |
| DL-11 | Parameter changes. | Assumption: apply only to new evaluations; each evaluation stores a snapshot of the parameters used. | Persist a parameter snapshot per evaluation. | open |
| DL-12 | Data correction after a decision. | Assumption: new extraction version, re-evaluation, full history kept. | Versioned extractions and evaluations; append-only history. | open |
| DL-13 | Override of a Rejected. | Adopted: a Rejected outcome is not final. An AP Analyst may override it with a mandatory comment; overriding an automatic outcome whose total exceeds the analyst limit requires the AP Manager. The audit trail keeps the original outcome and the human decision. Source: Recruitment reply, 12 Oct 2026. | Override is outside the engine (phase 4). | closed |
| DL-14 | Meaning of Auto-Pay. | Assumption: only records the decision, no payment integration. | No payment code. | open |
| DL-15 | Mapping invoice line to PO line. | Assumption: first by item code, then by description; mapping is stored; no mapping means hold. | Store the mapping per line. Implemented in the matching layer (phase 4) in this order: PO line printed on the document, identical normalized description, item code contained in the description. A method counts only if it finds exactly one PO line. | open |
| DL-16 | Documents that are not invoices (e.g. quotations). | Adopted: non-invoice documents are recorded with status NOT_AN_INVOICE before the engine runs, are never payable, and the intake user sees "received, not an invoice". Source: Recruitment reply, 12 Oct 2026. | Classifier before the engine (phase 3). | closed |
| DL-17 | Rounding in totals. | Adopted: 0.01 tolerance on totals and lines. Larger mismatches hold under A5. Source: Recruitment reply, 12 Oct 2026. | Rule A5 in the engine, using `rounding_tolerance`. | closed |
| DL-18 | Illegible field (quantity, price). | Adopted: illegible fields hold; the system never infers a value. Any suggested value must be labelled as a suggestion in the UI. Source: Recruitment reply, 12 Oct 2026. | Rule A6 in the engine, and a UI requirement for phase 5. | closed |
| DL-19 | Illegible or partial PO number. | Assumption: hold; the PO is not guessed. | No fuzzy PO matching. | open |
| DL-20 | VAT rate. | Assumption: arithmetic consistency is validated, not the rate. | No rate lookup. | open |
| DL-21 | Invoiced quantity accumulated across valid invoices of the same PO. | Adopted: only quantities already approved for payment count toward cumulative checks. A held or rejected invoice reserves nothing. If the new invoice exceeds the remaining received quantity, the whole invoice is held. Source: Recruitment reply, 12 Oct 2026. | The matching layer must track approvals (phase 4); engine unchanged (rule A8 reads `already_invoiced_by_po_line`). | closed |
| DL-22 | Real data not versioned in git. | Assumption: ignored via `.gitignore`; review later. | `data/reference/*.json` and `data/test-invoices/*` ignored. | open |
| DL-23 | (Added in Phase 1) Reference reload strategy and `DATABASE_URL` setting. | Reload replaces all reference rows in one transaction; source file versions are kept in `ref_sources` keyed by (file, sha256). `DATABASE_URL` added to Settings (default local SQLite). | Once invoices reference POs, replace-all must become versioned snapshots or upserts. | open |
| DL-24 | Reference data schema strictness. | Assumed: unknown fields are rejected, except Supplier.address, which is optional and stored. | A new field in the source files requires a schema change and a DL update. Referential checks run only against records that passed validation, so one root-cause error does not produce cascading errors. | open |
| DL-25 | Duplicate invoice definition. | Assumed: any earlier invoice with the same supplier_id and invoice_number, regardless of its outcome, makes the later one a duplicate. | A duplicate is rejected even if the first copy was held. Depends on processing order (upload timestamp). | open |
| DL-26 | Invoice date earlier than purchase order date. | Adopted: reject. | Rule R5, always enabled, not configurable. Source: challenge brief, section 7. | closed |
| DL-27 | Spec rules versus candidate assumptions. | Adopted: the five rejection and three hold conditions in spec section 7 are always enabled. Every additional rule derived from a decision-log assumption is tagged ASSUMPTION, carries its DL reference, and can be disabled by the AP Manager through `enabled_assumption_rules`. | A reviewer invoice always produces the outcome the spec prescribes, and the extra checks are visible and reversible. | open |
| DL-28 | All triggered rules are reported, not only the winning one. | Adopted: the engine evaluates every rule and applies precedence over the accumulated reasons. | An analyst sees every discrepancy on a held invoice in one pass. | open |
| DL-29 | Quotations under the literal spec. | Adopted: a document with no purchase order reference is rejected under R1. Classifying it as "not an invoice" (DL-16) is a pre-engine step. If AP prefers that, removing the classifier leaves R1 as the fallback. | Both paths are supported and the engine needs no change. | open |
| DL-30 | Zero unit price on the purchase order. | Assumed: the tolerance cannot be computed. An invoice price other than zero is a hold (H2). Equal zero prices do not hold. | Division by zero is never attempted. | open |
| DL-31 | Illegible purchase order number versus absent purchase order number. | Assumed: an invoice with a printed but partially illegible PO number is not R1 (absent). It is held under A7 (DL-19). | Extraction must distinguish "no PO printed" from "PO partially illegible" (`po_number_illegible`). | open |
| DL-32 | Line mapping when the purchase order cannot be determined. | Assumed: H3 is not evaluated when the invoice has no determinable PO (absent, illegible, or missing from the master). The PO-level rule already applies. | Avoids duplicate reasons on the same invoice. | open |
| DL-33 | Cumulative quantity check (A8) when the line alone is within received. | Assumed: A8 is reported only when the current line alone does not exceed received (H1 covers that case), so the same discrepancy is not reported twice. | The reason list has no duplicates. | open |
| DL-34 | Invoice supplier not resolved to the supplier master. | Assumed: when the invoice supplier cannot be matched to the master and a PO is referenced, R3 applies (reject). | The matching layer (phase 4) must report an unresolved supplier as None, not guess. | open |
| DL-35 | Missing or unreadable invoice number or invoice date. | Assumed: hold. The duplicate check (R4) and the date check (R5) cannot be evaluated, and the system does not infer the value. | Rule A9 (ASSUMPTION, holds), one reason per missing field. Disableable through `enabled_assumption_rules` without a code change. | open |
| DL-36 | Held invoices approved later by a human. | Adopted: an invoice that was held and is later approved for payment by an AP Analyst or AP Manager reserves its quantities from that moment, exactly like an AUTO_PAY invoice. Rejected invoices and invoices still held reserve nothing. | The matching layer (phase 4) must count AUTO_PAY plus human-approved invoices; the engine is unchanged; current tests only count AUTO_PAY because human approval does not exist yet. | open |
| DL-37 | Multiple PO references on one invoice. | Adopted (candidate decision): an invoice that references more than one purchase order is always held, so that an analyst confirms how it splits across the orders. | Rule A10 (ASSUMPTION, holds), switchable through `enabled_assumption_rules`. The mapper keeps the full list of PO references and a `MULTIPLE_PO_REFERENCES` flag; the matching layer looks up every referenced order. | closed |
| DL-38 | Division of work between the model and the code. | Assumed: the model classifies and transcribes; it never computes, corrects or maps lines to the purchase order. | Line-to-PO mapping is deterministic code (phase 4). The model reports a PO line number only when the document prints one. | open |
| DL-39 | Illegible versus absent fields, and suggested values. | Assumed: the model decides per field whether it was read, is illegible or is absent, and that status is shown to the analyst. Suggested values are displayed as suggestions and never used by the engine. | Status fields in the extraction schema; the mapper passes no suggestion on; a guess placed in the value of an unread field is demoted to a suggestion. | open |
| DL-40 | Extraction cache. | Assumed: keyed by file hash, prompt version, schema version and deployment. | Changing the file, the prompt, the schema or the deployment causes a new model call. Failed extractions are not cached. | open |
| DL-41 | Rate limit handling. | Assumed: `Retry-After` first, then exponential backoff with jitter; after 3 retries (configurable) the invoice is marked EXTRACTION_FAILED and can be retried. | Typed errors with a stable code are stored with the invoice. | open |
| DL-42 | PDF and upload handling. | Assumed: PDFs are rasterized at 170 DPI, up to 5 pages, and uploads are limited to 10 MB (all configurable). Larger documents are rejected with a clear message, not truncated. | No page is silently ignored. Supported types: PDF, PNG, JPG. | open |
| DL-43 | Supplier tax identifier format. | Assumed: supplier tax identifiers are normalized before matching (labels, spaces, dots and hyphens removed, upper case). The raw extracted value is kept for audit. Reason: in evaluation the model once returned the printed label with the value. | `normalize_tax_id` is applied to both the extracted value and the master value when they are compared; stored data is not changed. The prompt also asks for values without labels. | open |
| DL-44 | Storage of uploaded files. | Assumed: files are stored in a local folder as `data/uploads/<sha256>.<ext>`, behind a `FileStore` interface. The same file uploaded twice is stored once. | The folder is git-ignored. Deployment replaces the implementation with Azure Blob Storage without changing the services. | open |
| DL-45 | Submission and processing are separate steps. | Assumed: submitting an invoice stores the file and registers it as PENDING_EXTRACTION; extraction and evaluation run afterwards and can run in the background. | The upload answers at once and the intake user sees the status change. A failed extraction can be retried without uploading again. | open |
| DL-46 | Correction after a human decision. | Assumed: correcting data is not approving payment. An invoice whose data a person corrected never moves to Auto-Pay by itself: it is evaluated again, the evaluation keeps the engine outcome, and the invoice stays HELD_FOR_REVIEW until a person decides, whether or not there was a decision before. An earlier decision is marked as superseded. Reason: the recruitment reply asks to hold such cases for human confirmation, and a payment must not be a side effect of a data correction. | The invoice detail shows "Extracted data was corrected by <user>; a human decision is required before payment." The same person may correct and then approve, as two separate actions with their own comment and audit event; a second person is not required. If the engine rejects the corrected data, releasing payment needs an override. | open |
| DL-47 | Where permissions are enforced. | Assumed: in the services, not only in the API, with typed errors. | Any future entry point (API, script, background job) gets the same rules. Refused decision attempts are written to the audit trail. | open |
| DL-48 | Which human decisions apply to which status. | Assumed: a reviewer can decide on an invoice in any final status, including Auto-Pay, because the brief says an AP analyst may change the final status of any invoice. A new decision replaces the previous one, which stays in the history marked as superseded. A comment is always mandatory. | Still in force: the approval limit for approve and override (DL-49); a document that is not an invoice can never be approved for payment; intake users cannot decide; invoices pending or failed extraction cannot be decided. Approve applies when the engine did not reject the invoice and override when it did. Invoices evaluated earlier are not recomputed when a decision changes what counts as approved for payment. | open |
| DL-49 | Scope of the analyst approval limit. | Assumed: the limit applies to decisions that release payment (approve, override). The total must be strictly above the limit for the AP Manager to be required. An invoice with no readable total requires the AP Manager. Totals are compared with the limit as numbers, without currency conversion. | Rejecting or requesting a credit note is open to analysts at any amount. A multi-currency limit would need exchange rates. Limitation: totals are compared without currency conversion; all reference data is in USD. | open |
| DL-50 | Human corrections of extracted data. | Assumed: only an AP Analyst or the AP Manager can correct, with a mandatory comment. A value entered by a person counts as read, not as a suggestion. A correction can also reclassify the document. | A failed extraction has no data to correct: it is retried, not keyed in by hand. | open |
| DL-51 | What 'earlier invoice' means for duplicates. | Assumed: earlier by upload order, not by processing order. | The result does not depend on which of two copies finishes extraction first. An earlier upload that has not been extracted yet is not seen as a duplicate source. | open |
| DL-52 | Enforcement of the append-only audit trail. | Assumed: enforced by the application, which refuses any update or delete of an audit event. | Database-level protection (INSERT and SELECT only for the application user) is a deployment task. | open |
| DL-53 | Invoice history and reference reloads. | Assumed: invoices and evaluations keep supplier and purchase order identifiers as values, not foreign keys, together with the reference file hashes in force. | Reloading reference data (DL-23) cannot break or rewrite history. Past evaluations are not recomputed when reference data changes. | open |
| DL-54 | Ambiguous matches. | Assumed: a supplier or a PO line is matched only when exactly one candidate fits. Two candidates resolve to none. | An ambiguous supplier leads to R3 and an ambiguous line to H3, so a person decides instead of the system guessing. | open |
| DL-55 | Development authentication. | Assumed: `APP_ENV` defaults to `production`, where the development headers are always ignored and every request gets 401. Only when `APP_ENV=development` is set explicitly, in a local `.env`, do the user and role come from the `X-Dev-User` and `X-Dev-Role` headers. Reason: if the configuration is missing, the system is closed, not open. Microsoft Entra ID replaces the headers in phase 6 behind the same dependency. | A developer must add `APP_ENV=development` to `.env`; `.env.example` has it. The headers are a convenience, not security: in development anyone can claim any role. | open |
| DL-56 | Who sees the audit trail. | Assumed: the AP Manager only, as the brief gives the manager the full audit trail. | AP Analysts see, in the invoice detail, what they need to decide: extraction versions, the evaluation and every human decision. Intake users see neither. | open |
| DL-57 | Who reads the rule parameters. | Assumed: AP Analysts and the AP Manager can read the parameters in force; only the AP Manager changes them and sees the version history. Intake users cannot read them. | Analysts can see the tolerance and the approval limit that explain an outcome. | open |
| DL-58 | CORS. | Assumed: only the origin in `FRONTEND_ORIGIN` may call the API from a browser. | One frontend origin per environment. | open |
| DL-59 | Background processing. | Assumed: extraction and evaluation run in an in-process background task after the upload answers, with a database session of their own. An unexpected error while processing marks the invoice EXTRACTION_FAILED with code PROCESSING_ERROR and a plain message, writes an audit event, and the invoice can be retried like any other failure. | The error detail goes to the server log only; nothing internal is stored or shown. Whatever the failed attempt had written is rolled back. No queue: if the process itself stops mid-task the invoice stays PENDING_EXTRACTION and is not resumed. A durable queue is a deployment decision. | open |
| DL-60 | What intake users see. | Assumed: every invoice, not only their own uploads, limited to file name, upload time, status and plain-language reasons. Reviewer names and comments are not shown. Reason: the brief says the intake user can view the status of each submitted invoice, without restricting it to their own; intake is treated as a shared team inbox. | Rule reasons are shown as written by the engine, which includes PO prices and quantities. Restricting intake users to their own uploads would need a filter by uploader. | open |
| DL-61 | Upload checks at the API. | Assumed: an unsupported file type or an empty file is refused at upload (422), and a file over the limit with 413, instead of being registered and failing extraction. | Only readable documents become invoices. A corrupt PDF or image is still detected at extraction and can be retried. | open |
