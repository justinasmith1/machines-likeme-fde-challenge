# Contoso Invoice Three-Way Matching

## Overview

Contoso receives supplier invoices. Each invoice is matched against its purchase order (PO) and the goods received notes (GRN) and gets exactly one outcome: **Rejected**, **Held for Review** or **Auto-Pay**. A human (AP analyst / AP manager) reviews held items.

Only invoice data extraction uses an LLM (Azure OpenAI). The rules engine is pure, deterministic Python with unit tests.

Status: Phase 1 (project foundations). Extraction and the rules engine are not implemented yet.

## Architecture

_Placeholder. To be completed once extraction, engine, persistence and the frontend exist._

```
backend/app/
  domain/      enums and domain models
  reference/   JSON schemas + loader/validator for reference data
  rules/       engine interface + parameters (engine itself: Phase 2)
  db/          SQLAlchemy tables and session (SQLite now, Azure SQL later)
  cli.py       command line entry points
```

## Setup and running locally

Requires Python 3.12.

```bash
cd backend
python -m venv .venv
source .venv/Scripts/activate      # Windows Git Bash; PowerShell: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
cp ../.env.example ../.env         # then edit; never commit .env

pytest                             # run tests (from backend/)
uvicorn app.main:app --reload      # GET http://localhost:8000/health
```

## Loading reference data

Copy `suppliers.json`, `purchase_orders.json` and `goods_received_notes.json` into `data/reference/`, then from the repo root:

```bash
python -m app.cli load-reference --dir data/reference
```

The loader validates every record, checks referential integrity (PO→supplier, GRN→PO, GRN line→PO line) and either loads everything or fails with an aggregated error report (nothing is written on failure). Loading is idempotent. The SHA-256 of each source file and the load time are stored so the audit trail can state which reference version was used.

## Configuration

Read from environment variables / `.env` (see `.env.example`).

| Variable | Default | Notes |
|---|---|---|
| `AZURE_OPENAI_ENDPOINT` | – | Optional for now |
| `AZURE_OPENAI_API_KEY` | – | Secret; optional for now |
| `AZURE_OPENAI_DEPLOYMENT` | `JustinaSmith-RCS2` | |
| `PRICE_TOLERANCE_PCT` | `0.02` | |
| `ANALYST_APPROVAL_LIMIT` | `5000` | |
| `REFERENCE_DATA_DIR` | `data/reference` | |
| `DATABASE_URL` | `sqlite:///./contoso.db` | See DL-23 |

## Security notes

- Secrets live only in `.env` (git-ignored) or the deployment environment. `.env.example` has placeholders only.
- The API key is a `SecretStr`; it is never printed or logged.
- The model is called only from the server. The browser never receives keys or calls Azure OpenAI directly.
- Real reference data and test invoices are not committed (DL-22).

## Deployment

_Placeholder._

## Release process

_Placeholder._

## Decision log

These decisions are **the candidate's assumptions until recruiting responds**. All are provisional.

| ID | Question or assumption | Adopted answer | Consequence for implementation | Status |
|---|---|---|---|---|
| DL-01 | Inactive supplier: the spec does not mention it in the rejection rules. | Assumption: reject. | Rejection rule on `supplier.status == inactive`. | open |
| DL-02 | Price tolerance limit. | Assumption: inclusive, 2% over the PO unit price, per line. | `invoice_price <= po_price * (1 + tolerance)` per line. | open |
| DL-03 | Invoiced quantity lower than received. | Assumption: not a hold. | No rule triggers on under-invoicing. | open |
| DL-04 | Invoiced quantity above ordered but within received. | Assumption: hold as a precaution. | Hold rule when invoiced > ordered. | open |
| DL-05 | PO status closed or partially_invoiced. | Assumption: closed rejects; partially_invoiced is evaluated normally. | Rejection rule on closed PO. | open |
| DL-06 | Definition of duplicate. | Assumption: same supplier_id + invoice_number. | Uniqueness lookup on that pair. | open |
| DL-07 | Invoice currency differs from PO currency. | Assumption: hold. | Hold rule on currency mismatch. | open |
| DL-08 | Arithmetic inconsistency in totals. | Assumption: hold. | Hold rule on subtotal/tax/total mismatch. | open |
| DL-09 | Several POs on one invoice. | Assumption: if one fails, the whole invoice is rejected. | Evaluate per PO, combine with Rejected winning. | open |
| DL-10 | Line with no determinable PO line. | Assumption: hold (spec rule). | Hold rule on unmapped line. | open |
| DL-11 | Parameter changes. | Assumption: apply only to new evaluations; each evaluation stores a snapshot of the parameters used. | Persist a parameter snapshot per evaluation. | open |
| DL-12 | Data correction after a decision. | Assumption: new extraction version, re-evaluation, full history kept. | Versioned extractions and evaluations; append-only history. | open |
| DL-13 | Override of a Rejected. | Assumption: allowed for analyst and manager, mandatory comment. To be confirmed. | Role check plus required comment on override. | open |
| DL-14 | Meaning of Auto-Pay. | Assumption: only records the decision, no payment integration. | No payment code. | open |
| DL-15 | Mapping invoice line to PO line. | Assumption: first by item code, then by description; mapping is stored; no mapping means hold. | Store the mapping per line. | open |
| DL-16 | Documents that are not invoices (e.g. quotations). | Assumption: classified before the engine and shown with reason "not an invoice". To be confirmed with AP. | Classification step; `NOT_AN_INVOICE` status. | open |
| DL-17 | Rounding in totals. | Assumption: 0.01 tolerance on totals and lines; larger differences are a hold per DL-08. | Fixed 0.01 tolerance in arithmetic checks. | open |
| DL-18 | Illegible field (quantity, price). | Assumption: hold with reason "illegible field"; the system does not infer values. | Extraction fields are nullable; null triggers hold. | open |
| DL-19 | Illegible or partial PO number. | Assumption: hold; the PO is not guessed. | No fuzzy PO matching. | open |
| DL-20 | VAT rate. | Assumption: arithmetic consistency is validated, not the rate. | No rate lookup. | open |
| DL-21 | Invoiced quantity accumulated across valid invoices of the same PO. | Assumption: accumulated, not per invoice. To be confirmed with AP/Procurement. | Engine needs prior valid invoiced quantities per PO line. | open |
| DL-22 | Real data not versioned in git. | Assumption: ignored via `.gitignore`; review later. | `data/reference/*.json` and `data/test-invoices/*` ignored. | open |
| DL-23 | (Added in Phase 1) Reference reload strategy and `DATABASE_URL` setting. | Reload replaces all reference rows in one transaction; source file versions are kept in `ref_sources` keyed by (file, sha256). `DATABASE_URL` added to Settings (default local SQLite). | Once invoices reference POs, replace-all must become versioned snapshots or upserts. | open |
| DL-24 | Reference data schema strictness. | Assumed: unknown fields are rejected, except Supplier.address, which is optional and stored. | A new field in the source files requires a schema change and a DL update. Referential checks run only against records that passed validation, so one root-cause error does not produce cascading errors. | open |
| DL-25 | Duplicate invoice definition. | Assumed: any earlier invoice with the same supplier_id and invoice_number, regardless of its outcome, makes the later one a duplicate. | A duplicate is rejected even if the first copy was held. Depends on processing order (upload timestamp). | open |
