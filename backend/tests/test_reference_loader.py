import json
import shutil
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import make_session_factory
from app.db.tables import (
    GoodsReceivedNoteLineRow,
    PurchaseOrderLineRow,
    ReferenceSourceRow,
    SupplierRow,
)
from app.reference.loader import ReferenceDataError, load_reference

VALID = Path(__file__).parent / "fixtures" / "valid"


@pytest.fixture
def ref_dir(tmp_path: Path) -> Path:
    """Writable copy of the valid fixtures that tests can corrupt."""
    shutil.copytree(VALID, tmp_path / "ref")
    return tmp_path / "ref"


def _edit(path: Path, fn) -> None:
    data = json.loads(path.read_text())
    fn(data)
    path.write_text(json.dumps(data))


def _snapshot(session: Session) -> dict:
    return {
        "suppliers": session.scalar(select(func.count()).select_from(SupplierRow)),
        "po_lines": session.scalar(select(func.count()).select_from(PurchaseOrderLineRow)),
        "grn_lines": session.scalar(select(func.count()).select_from(GoodsReceivedNoteLineRow)),
        "sources": sorted(
            (r.file_name, r.sha256, r.loaded_at) for r in session.scalars(select(ReferenceSourceRow))
        ),
    }


def test_valid_load(session: Session):
    summary = load_reference(VALID, session)
    assert summary.counts == {
        "suppliers.json": 2,
        "purchase_orders.json": 2,
        "goods_received_notes.json": 1,
    }
    assert len(summary.sha256["suppliers.json"]) == 64
    assert _snapshot(session)["po_lines"] == 3
    assert _snapshot(session)["grn_lines"] == 2


def test_po_with_unknown_supplier_fails(ref_dir: Path, session: Session):
    _edit(ref_dir / "purchase_orders.json", lambda d: d[0].update(supplier_id="SUP-999"))
    with pytest.raises(ReferenceDataError) as exc:
        load_reference(ref_dir, session)
    assert any("SUP-999" in e for e in exc.value.errors)
    assert _snapshot(session)["suppliers"] == 0  # nothing written


def test_grn_with_unknown_po_fails(ref_dir: Path, session: Session):
    _edit(ref_dir / "goods_received_notes.json", lambda d: d[0].update(po_number="PO-2024-9999"))
    with pytest.raises(ReferenceDataError) as exc:
        load_reference(ref_dir, session)
    assert any("PO-2024-9999" in e for e in exc.value.errors)


def test_grn_with_unknown_po_line_fails(ref_dir: Path, session: Session):
    _edit(
        ref_dir / "goods_received_notes.json",
        lambda d: d[0]["lines"][0].update(po_line_number=99),
    )
    with pytest.raises(ReferenceDataError) as exc:
        load_reference(ref_dir, session)
    assert any("po_line_number 99" in e for e in exc.value.errors)


def test_errors_are_aggregated(ref_dir: Path, session: Session):
    _edit(ref_dir / "purchase_orders.json", lambda d: d[0].update(supplier_id="NOPE", currency="usd"))
    _edit(ref_dir / "goods_received_notes.json", lambda d: d[0].update(po_number="PO-2024-9999"))
    with pytest.raises(ReferenceDataError) as exc:
        load_reference(ref_dir, session)
    assert len(exc.value.errors) >= 2


def test_load_twice_is_idempotent(engine):
    factory = make_session_factory(engine)
    with factory() as s1:
        load_reference(VALID, s1)
        first = _snapshot(s1)
    with factory() as s2:
        load_reference(VALID, s2)
        second = _snapshot(s2)
    assert first == second


def test_supplier_with_address_loads(ref_dir: Path, session: Session):
    _edit(ref_dir / "suppliers.json", lambda d: d[0].update(address="1 Test Street, Testville"))
    load_reference(ref_dir, session)
    row = session.get(SupplierRow, "SUP-001")
    assert row.address == "1 Test Street, Testville"
    assert session.get(SupplierRow, "SUP-002").address is None


def test_supplier_with_unknown_field_still_fails(ref_dir: Path, session: Session):
    _edit(ref_dir / "suppliers.json", lambda d: d[0].update(phone="000"))
    with pytest.raises(ReferenceDataError) as exc:
        load_reference(ref_dir, session)
    assert any("phone" in e and "Extra inputs" in e for e in exc.value.errors)


def test_invalid_supplier_does_not_cascade_to_its_pos(ref_dir: Path, session: Session):
    _edit(ref_dir / "suppliers.json", lambda d: d[0].update(default_currency="usd"))
    with pytest.raises(ReferenceDataError) as exc:
        load_reference(ref_dir, session)
    assert len(exc.value.errors) == 1
    assert "SUP-001" in exc.value.errors[0]
    assert not any("unknown supplier_id" in e for e in exc.value.errors)
