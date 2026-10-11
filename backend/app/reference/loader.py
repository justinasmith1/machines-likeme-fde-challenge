"""Loads and validates the three reference JSON files into the database.

All validation happens before any write; any error aborts the load with an aggregated
report. Loading is idempotent: the reference tables are replaced inside one transaction,
and the (file, sha256) source record is only added if that version is not already known.
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.tables import (
    GoodsReceivedNoteLineRow,
    GoodsReceivedNoteRow,
    PurchaseOrderLineRow,
    PurchaseOrderRow,
    ReferenceSourceRow,
    SupplierRow,
)
from app.reference.schemas import GoodsReceivedNote, PurchaseOrder, Supplier

SUPPLIERS_FILE = "suppliers.json"
PURCHASE_ORDERS_FILE = "purchase_orders.json"
GRNS_FILE = "goods_received_notes.json"


class ReferenceDataError(Exception):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(f"{len(errors)} error(s) in reference data:\n" + "\n".join(errors))


@dataclass
class LoadSummary:
    counts: dict[str, int] = field(default_factory=dict)
    sha256: dict[str, str] = field(default_factory=dict)


def _read_records(path: Path, errors: list[str]) -> tuple[list[Any], str | None]:
    if not path.is_file():
        errors.append(f"{path.name}: file not found in {path.parent}")
        return [], None
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        errors.append(f"{path.name}: invalid JSON ({exc})")
        return [], digest
    # Accept a top-level list, or an object wrapping the list under a single key.
    if isinstance(data, dict) and len(data) == 1 and isinstance(next(iter(data.values())), list):
        data = next(iter(data.values()))
    if not isinstance(data, list):
        errors.append(f"{path.name}: expected a list of records")
        return [], digest
    return data, digest


def _validate[T: BaseModel](
    model: type[T], records: list[Any], file_name: str, id_field: str, errors: list[str]
) -> tuple[list[T], set[str]]:
    """Validate record by record. Also returns the ids of records that failed validation."""
    valid: list[T] = []
    failed_ids: set[str] = set()
    seen: set[str] = set()
    for i, rec in enumerate(records):
        label = f"{file_name}[{i}]"
        if isinstance(rec, dict) and id_field in rec:
            label += f" ({rec[id_field]})"
        try:
            obj = model.model_validate(rec)
        except ValidationError as exc:
            if isinstance(rec, dict) and isinstance(rec.get(id_field), str):
                failed_ids.add(rec[id_field])
            for err in exc.errors():
                loc = ".".join(str(p) for p in err["loc"])
                errors.append(f"{label}: {loc}: {err['msg']}")
            continue
        key = getattr(obj, id_field)
        if key in seen:
            errors.append(f"{label}: duplicate {id_field}")
            continue
        seen.add(key)
        valid.append(obj)
    return valid, failed_ids


def _check_integrity(
    suppliers: list[Supplier],
    pos: list[PurchaseOrder],
    grns: list[GoodsReceivedNote],
    failed_supplier_ids: set[str],
    failed_po_numbers: set[str],
    errors: list[str],
) -> None:
    """Referential checks over records that passed validation only.

    References to records that failed validation are skipped: their own error is the root cause.
    """
    supplier_ids = {s.supplier_id for s in suppliers}
    po_lines = {po.po_number: {ln.line_number for ln in po.lines} for po in pos}
    for po in pos:
        if po.supplier_id not in supplier_ids and po.supplier_id not in failed_supplier_ids:
            errors.append(
                f"{PURCHASE_ORDERS_FILE} ({po.po_number}): unknown supplier_id {po.supplier_id}"
            )
    for grn in grns:
        if grn.po_number in failed_po_numbers:
            continue
        if grn.po_number not in po_lines:
            errors.append(f"{GRNS_FILE} ({grn.grn_number}): unknown po_number {grn.po_number}")
            continue
        for ln in grn.lines:
            if ln.po_line_number not in po_lines[grn.po_number]:
                errors.append(
                    f"{GRNS_FILE} ({grn.grn_number}): po_line_number {ln.po_line_number} "
                    f"does not exist in {grn.po_number}"
                )


def load_reference(directory: Path, session: Session) -> LoadSummary:
    errors: list[str] = []
    summary = LoadSummary()

    raw: dict[str, list[Any]] = {}
    for name in (SUPPLIERS_FILE, PURCHASE_ORDERS_FILE, GRNS_FILE):
        records, digest = _read_records(directory / name, errors)
        raw[name] = records
        if digest:
            summary.sha256[name] = digest

    suppliers, bad_suppliers = _validate(
        Supplier, raw[SUPPLIERS_FILE], SUPPLIERS_FILE, "supplier_id", errors
    )
    pos, bad_pos = _validate(
        PurchaseOrder, raw[PURCHASE_ORDERS_FILE], PURCHASE_ORDERS_FILE, "po_number", errors
    )
    grns, _ = _validate(GoodsReceivedNote, raw[GRNS_FILE], GRNS_FILE, "grn_number", errors)
    _check_integrity(suppliers, pos, grns, bad_suppliers, bad_pos, errors)

    if errors:
        raise ReferenceDataError(errors)

    now = datetime.now(UTC).replace(tzinfo=None)
    with session.begin():
        for table in (
            GoodsReceivedNoteLineRow,
            GoodsReceivedNoteRow,
            PurchaseOrderLineRow,
            PurchaseOrderRow,
            SupplierRow,
        ):
            session.execute(delete(table))
        session.add_all(SupplierRow(**s.model_dump()) for s in suppliers)
        session.flush()
        session.add_all(
            PurchaseOrderRow(
                **po.model_dump(exclude={"lines"}),
                lines=[PurchaseOrderLineRow(**ln.model_dump()) for ln in po.lines],
            )
            for po in pos
        )
        session.flush()
        session.add_all(
            GoodsReceivedNoteRow(
                **g.model_dump(exclude={"lines"}),
                lines=[GoodsReceivedNoteLineRow(**ln.model_dump()) for ln in g.lines],
            )
            for g in grns
        )
        for name, count in (
            (SUPPLIERS_FILE, len(suppliers)),
            (PURCHASE_ORDERS_FILE, len(pos)),
            (GRNS_FILE, len(grns)),
        ):
            summary.counts[name] = count
            if session.get(ReferenceSourceRow, (name, summary.sha256[name])) is None:
                session.add(
                    ReferenceSourceRow(
                        file_name=name,
                        sha256=summary.sha256[name],
                        record_count=count,
                        loaded_at=now,
                    )
                )
    return summary
