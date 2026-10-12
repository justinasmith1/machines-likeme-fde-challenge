"""Map a validated model extraction to the ExtractedInvoice the rules engine consumes.

Rules of the mapping:
  - quantity / unit_price not "read"      -> None (engine rule A6).
  - invoice_number / invoice_date not "read" -> None (engine rule A9).
  - po_references "illegible"             -> no PO and po_number_illegible=True (A7, not R1).
  - po_references "absent"                -> no PO (R1).
  - Suggested values never reach the engine (DL-18, DL-39).
  - supplier_tax_id is passed on as extracted; a normalized copy is added for matching
    against the supplier master (DL-43).
  - More than one PO reference: the engine takes one PO per invoice, so the first is
    mapped, the full list is kept and a visible flag is added (DL-37).
"""

from dataclasses import dataclass, field

from app.domain.identifiers import normalize_tax_id
from app.domain.models import ExtractedInvoice, ExtractedInvoiceLine
from app.extraction.schema import ModelExtraction

MULTIPLE_PO_REFERENCES = "MULTIPLE_PO_REFERENCES"


@dataclass(frozen=True)
class MappedInvoice:
    invoice: ExtractedInvoice
    po_references: list[str]  # every PO number read from the document
    line_po_references: list[int | None]  # po_line_reference per line, as printed
    # For matching against the supplier master only (DL-43). The raw extracted value
    # stays in invoice.supplier_tax_id for the audit trail.
    supplier_tax_id_normalized: str | None = None
    flags: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # plain-language text for the flags


def to_engine_input(extraction: ModelExtraction) -> MappedInvoice:
    header = extraction.header
    references = list(header.po_references.values)  # empty unless status is "read"

    flags: list[str] = []
    notes: list[str] = []
    if len(references) > 1:
        flags.append(MULTIPLE_PO_REFERENCES)
        notes.append(
            f"The invoice references {len(references)} purchase orders "
            f"({', '.join(references)}). Only the first one was matched automatically."
        )

    invoice = ExtractedInvoice(
        supplier_name=header.supplier_name,
        supplier_tax_id=header.supplier_tax_id,
        invoice_number=header.invoice_number.value,
        invoice_date=header.invoice_date.value,
        po_number=references[0] if references else None,
        po_number_illegible=header.po_references.status == "illegible",
        currency=header.currency,
        lines=[
            ExtractedInvoiceLine(
                description=line.description,
                quantity=line.quantity.value,
                unit_price=line.unit_price.value,
                line_total=line.line_total,
            )
            for line in extraction.lines
        ],
        subtotal=header.subtotal,
        tax_amount=header.tax_amount,
        total=header.total_amount,
    )
    return MappedInvoice(
        invoice=invoice,
        po_references=references,
        line_po_references=[line.po_line_reference for line in extraction.lines],
        supplier_tax_id_normalized=normalize_tax_id(header.supplier_tax_id),
        flags=flags,
        notes=notes,
    )
