"""Normalization of identifiers for matching. Stored and audited values stay untouched."""

import re

# Printed labels that may precede a tax identifier. Longest first so that "VAT Reg No"
# is not cut short by "VAT". A label only counts when a separator follows it, so an
# identifier that merely starts with the same letters is left alone.
_TAX_ID_LABELS = (
    r"vat\s*reg(?:istration)?\.?\s*no\.?",
    r"vat\s*no\.?",
    r"ust-?\s*idnr\.?",
    r"tax\s*id",
    r"vat",
    r"tin",
    r"ein",
    r"cuit",
)
_LABEL_PREFIX = re.compile(
    r"^\s*(?:" + "|".join(_TAX_ID_LABELS) + r")\s*(?:[:#]\s*|\s+)", re.IGNORECASE
)
_SEPARATORS = re.compile(r"[\s.\-]")


def normalize_tax_id(value: str | None) -> str | None:
    """Comparable form of a supplier tax identifier (DL-43).

    Removes a leading printed label ("Tax ID:", "VAT No", "CUIT"...), then spaces, dots
    and hyphens, and upper-cases the rest. "12-3456789" and "123456789" compare equal.
    Use it on both sides of a comparison; never store the result in place of the original.
    """
    if value is None:
        return None
    text = _LABEL_PREFIX.sub("", value, count=1)
    text = _SEPARATORS.sub("", text).upper()
    return text or None
