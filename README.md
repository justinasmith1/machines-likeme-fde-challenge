# Contoso Invoice Three-Way Matching

## Overview

Contoso receives supplier invoices. Each invoice is matched against its purchase order (PO) and the goods received notes (GRN) and gets exactly one outcome: **Rejected**, **Held for Review** or **Auto-Pay**. A human (AP analyst / AP manager) reviews held items.

Only invoice data extraction uses an LLM (Azure OpenAI). The rules engine is pure, deterministic Python with unit tests.

Status: reference data loader (phase 1), rules engine (phase 2) and invoice extraction (phase 3) are implemented. The matching layer, persistence of invoices, the API and the frontend are not implemented yet.

## Architecture

_Placeholder. To be completed once extraction, engine, persistence and the frontend exist._

```
backend/app/
  domain/      enums and domain models
  reference/   JSON schemas + loader/validator for reference data
  extraction/  document rendering, Azure OpenAI client, cache, mapping to engine input
  rules/       deterministic rules engine and its parameters
  db/          SQLAlchemy tables and session (SQLite now, Azure SQL later)
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
5. **Decision** (`extraction/pipeline.py`). Only a document classified as an invoice reaches the rules engine.

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

## Configuration

Read from environment variables / `.env` (see `.env.example`).

| Variable | Default | Notes |
|---|---|---|
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

## Security notes

- Secrets live only in `.env` (git-ignored) or the deployment environment. `.env.example` has placeholders only.
- The API key is a `SecretStr`; it is never printed or logged. It is sent only in the request header, and error text returned by the service is masked before it is stored or shown.
- The model is called only from the server. The browser never receives keys or calls Azure OpenAI directly.
- Real reference data and test invoices are not committed (DL-22).
- Model responses are cached under `data/cache/`, which is git-ignored because it contains real invoice data.
- Tests never reach the network: a test guard fails on any real connection.

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
| DL-15 | Mapping invoice line to PO line. | Assumption: first by item code, then by description; mapping is stored; no mapping means hold. | Store the mapping per line. | open |
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
| DL-37 | Multiple PO references on one invoice. | Assumed: the rules engine evaluates one purchase order per invoice. The first reference read from the document is matched, the full list is kept with the extraction, and the invoice carries a visible `MULTIPLE_PO_REFERENCES` flag with a plain-language note. | The engine is unchanged. The flag does not by itself hold the invoice: whether a flagged invoice must always be held, and how lines on the other purchase orders are checked (DL-09), is decided in the matching layer (phase 4). | open |
| DL-38 | Division of work between the model and the code. | Assumed: the model classifies and transcribes; it never computes, corrects or maps lines to the purchase order. | Line-to-PO mapping is deterministic code (phase 4). The model reports a PO line number only when the document prints one. | open |
| DL-39 | Illegible versus absent fields, and suggested values. | Assumed: the model decides per field whether it was read, is illegible or is absent, and that status is shown to the analyst. Suggested values are displayed as suggestions and never used by the engine. | Status fields in the extraction schema; the mapper passes no suggestion on; a guess placed in the value of an unread field is demoted to a suggestion. | open |
| DL-40 | Extraction cache. | Assumed: keyed by file hash, prompt version, schema version and deployment. | Changing the file, the prompt, the schema or the deployment causes a new model call. Failed extractions are not cached. | open |
| DL-41 | Rate limit handling. | Assumed: `Retry-After` first, then exponential backoff with jitter; after 3 retries (configurable) the invoice is marked EXTRACTION_FAILED and can be retried. | Typed errors with a stable code are stored with the invoice. | open |
| DL-42 | PDF and upload handling. | Assumed: PDFs are rasterized at 170 DPI, up to 5 pages, and uploads are limited to 10 MB (all configurable). Larger documents are rejected with a clear message, not truncated. | No page is silently ignored. Supported types: PDF, PNG, JPG. | open |
| DL-43 | Supplier tax identifier format. | Assumed: supplier tax identifiers are normalized before matching (labels, spaces, dots and hyphens removed, upper case). The raw extracted value is kept for audit. Reason: in evaluation the model once returned the printed label with the value. | `normalize_tax_id` is applied to both the extracted value and the master value when they are compared; stored data is not changed. The prompt also asks for values without labels. | open |
