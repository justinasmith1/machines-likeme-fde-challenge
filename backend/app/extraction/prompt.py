"""Instruction sent to the model with the document images.

Bump PROMPT_VERSION on any change: it is part of the cache key (DL-40).
"""

import json

from app.extraction.schema import EXTRACTION_JSON_SCHEMA

PROMPT_VERSION = "2"

_INSTRUCTIONS = """\
You transcribe supplier documents for an accounts payable team. The images are the pages \
of one document, in order. Return one JSON object that follows the schema.

Step 1. Classify the document.
- "invoice": a supplier's request for payment for goods or services delivered.
- "quotation": a quote, estimate, offer or pro forma. It is not a request for payment.
- "credit_note": a credit note or credit memo.
- "other": anything else.
Give a short reason in classification_reason. Then fill in the remaining fields as far as \
the document shows them, whatever its type.

Step 2. Transcribe only what is printed.
- Return field values only, without their printed labels (for example, return \
'AB123456789', not 'Tax ID: AB123456789').
- Copy values exactly as printed. Do not calculate, correct, complete or reformat \
identifiers. If a total does not add up, transcribe it as printed.
- Amounts are decimal strings with a dot and no thousands separator, for example \
"1514.00". Do not include currency symbols.
- Dates are YYYY-MM-DD.
- For a quotation, put the quotation number in invoice_number and its date in invoice_date.

Step 3. Report how each critical field was read. invoice_number, invoice_date, \
po_references, and each line's quantity and unit_price have a status:
- "read": printed and fully legible. Put the value in value.
- "illegible": printed, but any digit or character is covered, smudged, blurred, cut off \
or otherwise unreadable. Set value to null. This applies even when you can deduce the \
value from other numbers on the page.
- "absent": the field is not printed on the document at all. Set value to null.
A partly readable number is "illegible", not "read". Never put a guess in value.
When a field is illegible and you can infer it (for example quantity from line total \
divided by unit price, or a purchase order number from its visible digits), put the \
inference in suggested_value (or suggested_values). It will be shown to a person as a \
suggestion only.

Purchase orders.
- po_references lists every purchase order number printed on the document.
- If the document shows no purchase order number, status is "absent" and values is empty.
- If a purchase order number is printed but not fully legible, status is "illegible" and \
values is empty.
- po_line_reference is the purchase order line number of an invoice line, only if the \
document prints it. Otherwise null. Do not work it out from the order of the lines.
"""


def build_prompt(*, include_schema: bool) -> str:
    """Prompt text. The schema is appended when structured outputs are not available."""
    if not include_schema:
        return _INSTRUCTIONS
    return (
        _INSTRUCTIONS
        + "\nReturn only the JSON object, with no markdown and no commentary. "
        + "It must validate against this JSON schema:\n"
        + json.dumps(EXTRACTION_JSON_SCHEMA)
    )
