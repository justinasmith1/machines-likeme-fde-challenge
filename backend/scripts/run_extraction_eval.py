"""Manual evaluation of the extraction against the real Azure OpenAI deployment.

NOT part of pytest. It calls the paid, rate-limited API (about 3 calls per minute), so it
is run by hand:

    cd backend
    python scripts/run_extraction_eval.py                 # every document, using the cache
    python scripts/run_extraction_eval.py --only INV-06   # one document
    python scripts/run_extraction_eval.py --no-cache      # force new model calls

For each document in data/test-invoices/ it:
  1. extracts it (cache first; waits between real calls to respect the rate limit);
  2. compares the model output field by field with the hand-verified extraction in
     data/test-invoices/extracted/INV-XX.extracted.json;
  3. runs the mapper and the rules engine and compares the result with the golden set.

It prints a summary table and writes the detail to data/cache/eval_report.json.
Everything it reads and writes is under data/, which is git-ignored.

Line mapping: the model only reports a PO line when the document prints one (DL-38).
Until the matching layer exists (phase 4), a line without a printed reference is mapped
here by exact description match against the purchase order. The report says which source
was used for each line.
"""

import argparse
import json
import sys
import time
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "tests"))

from app.config import get_settings  # noqa: E402
from app.domain.enums import InvoiceStatus  # noqa: E402
from app.domain.identifiers import normalize_tax_id  # noqa: E402
from app.domain.models import ExtractedInvoice  # noqa: E402
from app.extraction.client import AzureExtractionClient, require_credentials  # noqa: E402
from app.extraction.errors import MissingCredentials  # noqa: E402
from app.extraction.mapper import to_engine_input  # noqa: E402
from app.extraction.pipeline import decide  # noqa: E402
from app.extraction.service import ExtractionStatus, extract  # noqa: E402
from app.rules.engine import evaluate  # noqa: E402
from app.rules.parameters import RuleParameters  # noqa: E402
from golden_harness import (  # noqa: E402
    GOLDEN,
    INVOICES_DIR,
    REFERENCE_DIR,
    GoldenState,
    line_mapping,
    verified_extraction_path,
)

HEADER_FIELDS = (
    "supplier_name",
    "supplier_tax_id",
    "invoice_number",
    "invoice_date",
    "due_date",
    "po_number",
    "currency",
    "subtotal",
    "tax_amount",
    "total",
)
LINE_FIELDS = ("description", "quantity", "unit_price", "line_total")


# ---- comparison helpers


def _normalise(value):
    """Comparable form: decimals by value, dates as ISO text, text without case or spacing."""
    if value is None:
        return None
    if isinstance(value, (Decimal, int, float)):
        return Decimal(str(value))
    if isinstance(value, date):
        return value.isoformat()
    text = " ".join(str(value).split())
    try:
        return Decimal(text)
    except InvalidOperation:
        return text.casefold()


def _same(expected, actual) -> bool:
    return _normalise(expected) == _normalise(actual)


def _show(value):
    return None if value is None else str(value)


def _model_header(extraction) -> dict:
    header = extraction.header
    return {
        "supplier_name": header.supplier_name,
        "supplier_tax_id": header.supplier_tax_id,
        "invoice_number": header.invoice_number.value,
        "invoice_date": header.invoice_date.value,
        "due_date": header.due_date,
        "po_number": header.po_references.values[0] if header.po_references.values else None,
        "currency": header.currency,
        "subtotal": header.subtotal,
        "tax_amount": header.tax_amount,
        "total": header.total_amount,
    }


def _model_line(line) -> dict:
    return {
        "description": line.description,
        "quantity": line.quantity.value,
        "unit_price": line.unit_price.value,
        "line_total": line.line_total,
    }


def compare_with_verified(extraction, verified: dict) -> dict:
    """Field-by-field comparison of the model output with the hand-verified extraction."""
    header_diffs = []
    model_header = _model_header(extraction)
    for name in HEADER_FIELDS:
        if name not in verified:
            continue  # the verified file does not record this field
        if name == "supplier_tax_id":
            # Compared the way the matching does it (DL-43), so a label or a hyphen is
            # not reported as a difference. The raw value is kept in the report below.
            same = normalize_tax_id(verified[name]) == normalize_tax_id(model_header[name])
        else:
            same = _same(verified[name], model_header[name])
        if not same:
            header_diffs.append(
                {"field": name, "expected": _show(verified[name]), "model": _show(model_header[name])}
            )

    line_diffs = []
    expected_lines = verified.get("lines", [])
    if len(expected_lines) != len(extraction.lines):
        line_diffs.append(
            {"field": "line count", "expected": len(expected_lines), "model": len(extraction.lines)}
        )
    for index, (expected, line) in enumerate(zip(expected_lines, extraction.lines), start=1):
        model_line = _model_line(line)
        for name in LINE_FIELDS:
            if name in expected and not _same(expected[name], model_line[name]):
                line_diffs.append(
                    {
                        "line": index,
                        "field": name,
                        "expected": _show(expected[name]),
                        "model": _show(model_line[name]),
                    }
                )

    # Fields the reviewer found illegible must be reported as illegible, not read or absent.
    illegible_checks = []
    if verified.get("po_number_illegible"):
        status = extraction.header.po_references.status
        illegible_checks.append(
            {
                "field": "po_references",
                "model_status": status,
                "ok": status == "illegible",
                "suggested": extraction.header.po_references.suggested_values,
            }
        )
    for index, (expected, line) in enumerate(zip(expected_lines, extraction.lines), start=1):
        for name in ("quantity", "unit_price"):
            if name in expected and expected[name] is None:
                field = getattr(line, name)
                illegible_checks.append(
                    {
                        "field": f"line {index} {name}",
                        "model_status": field.status,
                        "ok": field.status == "illegible",
                        "suggested": _show(field.suggested_value),
                    }
                )

    return {
        "supplier_tax_id_raw": model_header["supplier_tax_id"],
        "supplier_tax_id_normalized": normalize_tax_id(model_header["supplier_tax_id"]),
        "header_ok": not header_diffs,
        "header_diffs": header_diffs,
        "lines_ok": not line_diffs,
        "line_diffs": line_diffs,
        "illegible_ok": all(check["ok"] for check in illegible_checks),
        "illegible_checks": illegible_checks,
    }


def map_lines(state: GoldenState, mapped) -> tuple[dict, list[str]]:
    """Line mapping for the engine: printed PO line first, then exact description match."""
    po_number = mapped.invoice.po_number
    po = state.purchase_orders.get(po_number) if po_number else None
    references: list[int | None] = []
    sources: list[str] = []
    for line, printed in zip(mapped.invoice.lines, mapped.line_po_references):
        if printed is not None:
            references.append(printed)
            sources.append("printed on document")
            continue
        match = None
        if po is not None and line.description:
            wanted = _normalise(line.description)
            candidates = [ln.line_number for ln in po.lines if _normalise(ln.description) == wanted]
            if len(candidates) == 1:
                match = candidates[0]
        references.append(match)
        sources.append("description match" if match is not None else "unmapped")
    return line_mapping(po_number, references), sources


# ---- main


def _feed_state_from_verified(state: GoldenState, filename: str) -> None:
    """Keep document order meaningful for documents that are not being evaluated now."""
    path = verified_extraction_path(filename)
    if not path.is_file():
        return
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("document_type", "invoice") != "invoice":
        return
    invoice = ExtractedInvoice.model_validate(raw)
    mapping = line_mapping(invoice.po_number, [ln.get("po_line_reference") for ln in raw["lines"]])
    decision = evaluate(invoice, state.build_context(invoice, mapping), RuleParameters())
    state.record(invoice, mapping, decision)


def evaluate_document(case: dict, state: GoldenState, settings, client, use_cache: bool) -> dict:
    filename = case["filename"]
    report: dict = {"file": filename, "expected_outcome": case["expected_outcome"]}
    source = INVOICES_DIR / filename
    if not source.is_file():
        report["error"] = f"file not found: {source}"
        return report

    result = extract(
        source.read_bytes(), filename, settings=settings, client=client, use_cache=use_cache
    )
    report.update(
        {
            "extraction_status": result.status.value,
            "from_cache": result.from_cache,
            "tokens": result.tokens,
            "structured_output": result.structured_output,
            "document_type": result.document_type,
            "classification_reason": result.classification_reason,
        }
    )
    if result.status is ExtractionStatus.EXTRACTION_FAILED:
        report["error"] = f"{result.error_code}: {'; '.join(result.errors)}"
        report["outcome_ok"] = False
        return report

    verified_path = verified_extraction_path(filename)
    if verified_path.is_file():
        verified = json.loads(verified_path.read_text(encoding="utf-8"))
        expected_type = verified.get("document_type", "invoice")
        report["classification_ok"] = result.document_type == expected_type
        report["expected_document_type"] = expected_type
        if expected_type == "invoice" and result.extraction is not None:
            report.update(compare_with_verified(result.extraction, verified))
    else:
        report["note"] = "no hand-verified extraction to compare with"

    mapping_sources: list[str] = []
    mapping_holder: dict = {}

    def build_context(mapped):
        mapping, sources = map_lines(state, mapped)
        mapping_holder["mapping"] = mapping
        mapping_sources.extend(sources)
        return state.build_context(mapped.invoice, mapping)

    outcome = decide(result, build_context, RuleParameters())
    if outcome.decision is not None:
        state.record(outcome.mapped.invoice, mapping_holder["mapping"], outcome.decision)
        report["reasons"] = [f"{r.code}: {r.message}" for r in outcome.decision.reasons]
    report["status"] = outcome.status.value
    report["flags"] = outcome.flags
    report["line_mapping_sources"] = mapping_sources
    if case["expected_outcome"] is None:
        report["outcome_ok"] = outcome.status is InvoiceStatus.NOT_AN_INVOICE
    else:
        report["outcome_ok"] = outcome.status.value == case["expected_outcome"]
    return report


def _mark(value) -> str:
    return {True: "ok", False: "DIFF", None: "-"}[value]


def print_summary(reports: list[dict]) -> None:
    columns = ("file", "class", "header", "lines", "illegible", "expected", "got", "outcome", "source")
    rows = []
    for r in reports:
        if "extraction_status" not in r or r.get("extraction_status") == "EXTRACTION_FAILED":
            rows.append((r["file"], "-", "-", "-", "-", str(r["expected_outcome"]), "FAILED", "DIFF", "-"))
            continue
        rows.append(
            (
                r["file"],
                _mark(r.get("classification_ok")),
                _mark(r.get("header_ok")),
                _mark(r.get("lines_ok")),
                _mark(r.get("illegible_ok")) if r.get("illegible_checks") else "-",
                str(r["expected_outcome"]),
                r.get("status", "-"),
                _mark(r.get("outcome_ok")),
                "cache" if r.get("from_cache") else "model",
            )
        )
    widths = [max(len(columns[i]), *(len(row[i]) for row in rows)) for i in range(len(columns))]
    print("  ".join(name.ljust(widths[i]) for i, name in enumerate(columns)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))

    for r in reports:
        problems = []
        if r.get("error"):
            problems.append(r["error"])
        problems += [f"header {d['field']}: expected {d['expected']!r}, model {d['model']!r}" for d in r.get("header_diffs", [])]
        problems += [
            f"line {d.get('line', '-')} {d['field']}: expected {d['expected']!r}, model {d['model']!r}"
            for d in r.get("line_diffs", [])
        ]
        problems += [
            f"{c['field']}: expected illegible, model said {c['model_status']}"
            for c in r.get("illegible_checks", [])
            if not c["ok"]
        ]
        if r.get("outcome_ok") is False and not r.get("error"):
            problems.append(f"outcome: expected {r['expected_outcome']}, got {r.get('status')} {r.get('reasons', [])}")
        if problems:
            print(f"\n{r['file']}")
            for problem in problems:
                print(f"  - {problem}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--only", help="Evaluate one document, for example INV-06")
    parser.add_argument("--no-cache", action="store_true", help="Ignore cached extractions")
    parser.add_argument(
        "--min-interval",
        type=float,
        default=21.0,
        help="Seconds between real model calls (default 21, about 3 per minute)",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    try:
        require_credentials(settings)
    except MissingCredentials as exc:
        print(f"Cannot run the evaluation: {exc}", file=sys.stderr)
        return 2

    state = GoldenState.load()
    if state is None:
        print(f"Reference data not found in {REFERENCE_DIR}.", file=sys.stderr)
        return 2

    selected = [c for c in GOLDEN if not args.only or Path(c["filename"]).stem == args.only]
    if not selected:
        print(f"No document named {args.only} in the golden set.", file=sys.stderr)
        return 2

    client = AzureExtractionClient.from_settings(settings)
    reports: list[dict] = []
    last_model_call: float | None = None
    for case in GOLDEN:
        if case not in selected:
            _feed_state_from_verified(state, case["filename"])
            continue
        if last_model_call is not None:
            wait = args.min_interval - (time.monotonic() - last_model_call)
            if wait > 0:
                print(f"Waiting {wait:.0f}s to respect the rate limit...")
                time.sleep(wait)
        print(f"Processing {case['filename']}...")
        report = evaluate_document(case, state, settings, client, use_cache=not args.no_cache)
        if not report.get("from_cache") and "extraction_status" in report:
            last_model_call = time.monotonic()
        reports.append(report)

    print()
    print_summary(reports)

    total_tokens = sum((r.get("tokens") or {}).get("total_tokens", 0) for r in reports)
    modes = {r["structured_output"] for r in reports if r.get("structured_output") is not None}
    print(f"\nTokens used in this run (cached documents count 0): {total_tokens}")
    if modes:
        print(
            "Output mode: "
            + ", ".join("structured outputs (json_schema)" if m else "JSON in prompt (fallback)" for m in modes)
        )

    report_path = settings.extraction_cache_path.parent / "eval_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(reports, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"Detail written to {report_path}")
    return 0 if all(r.get("outcome_ok") for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
