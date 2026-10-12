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
