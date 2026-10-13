import pytest

from app.db.tables import SupplierRow
from app.domain.identifiers import normalize_name, normalize_tax_id
from app.extraction.mapper import to_engine_input
from app.matching.matcher import resolve_supplier
from extraction_builders import make_extraction

MASTER = "AB556123456701"


@pytest.mark.parametrize(
    "raw",
    [
        "AB556123456701",
        "Tax ID: AB556123456701",  # label with colon
        "Tax ID AB556123456701",  # label without colon
        "tax id: ab556123456701",  # lower case
        "TAX ID:AB556123456701",
        "VAT: AB556123456701",
        "VAT No AB556123456701",
        "VAT No. AB556123456701",
        "VAT Reg No: AB556123456701",
        "TIN: AB556123456701",
        "EIN AB556123456701",
        "CUIT: AB556123456701",
        "USt-IdNr: AB556123456701",
        "USt-IdNr. AB556123456701",
        "AB 556 123 456 701",  # spaces
        "AB-556123-456701",  # hyphens
        "AB.556.123.456.701",  # dots
        "  Tax ID:  AB 556-123.456701  ",
    ],
)
def test_variants_normalize_to_the_master_value(raw):
    assert normalize_tax_id(raw) == MASTER


def test_hyphenated_and_plain_forms_match():
    assert normalize_tax_id("12-3456789") == normalize_tax_id("123456789") == "123456789"


def test_different_identifier_still_does_not_match():
    assert normalize_tax_id("Tax ID: AB556123456702") != normalize_tax_id(MASTER)


def test_label_is_only_removed_when_a_separator_follows():
    # Identifiers that merely start with the same letters as a label keep them.
    assert normalize_tax_id("TIN123456") == "TIN123456"
    assert normalize_tax_id("EIN-123456") == "EIN123456"
    assert normalize_tax_id("VATX99") == "VATX99"


@pytest.mark.parametrize("raw", [None, "", "   ", "Tax ID:"])
def test_nothing_usable_normalizes_to_none(raw):
    assert normalize_tax_id(raw) is None


def test_mapper_keeps_the_raw_value_and_adds_the_normalized_one():
    extraction = make_extraction(header={"supplier_tax_id": "Tax ID: tax-b"})
    mapped = to_engine_input(extraction)
    assert mapped.invoice.supplier_tax_id == "Tax ID: tax-b"  # raw, for audit
    assert mapped.supplier_tax_id_normalized == "TAXB"  # for matching only
    assert extraction.header.supplier_tax_id == "Tax ID: tax-b"


def _add_supplier(session, tax_id: str) -> None:
    session.add(
        SupplierRow(
            supplier_id="SUP-002",
            legal_name="Test Supplier B",
            tax_id=tax_id,
            default_currency="EUR",
            status="active",
        )
    )
    session.flush()


def test_supplier_is_resolved_despite_label_and_format_differences(session):
    _add_supplier(session, "12-3456789")  # master keeps its own format
    for extracted in ("Tax ID: 123456789", "12-3456789", "ein 12 3456789"):
        supplier, method = resolve_supplier(session, extracted, None)
        assert supplier is not None and supplier.tax_id == "12-3456789"
        assert method == "TAX_ID"


def test_supplier_with_a_different_tax_id_is_not_resolved(session):
    _add_supplier(session, "12-3456789")
    supplier, method = resolve_supplier(session, "Tax ID: 12-3456780", None)
    assert supplier is None and method == "UNRESOLVED"


def test_names_normalize_to_letters_and_digits_in_lower_case():
    assert normalize_name("Example Trading S.A.") == normalize_name("example  trading sa") == "exampletradingsa"
    assert normalize_name("Example Trading") != normalize_name("Example Traders")
    assert normalize_name(None) is None and normalize_name(" . ") is None
