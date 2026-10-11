"""SQLAlchemy tables. Only reference data for now. Types chosen to port to Azure SQL."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

MONEY = Numeric(18, 4)


class Base(DeclarativeBase):
    pass


class SupplierRow(Base):
    __tablename__ = "ref_suppliers"

    supplier_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    legal_name: Mapped[str] = mapped_column(String(256))
    tax_id: Mapped[str] = mapped_column(String(64))
    default_currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(16))
    address: Mapped[str | None] = mapped_column(String(512), nullable=True)


class PurchaseOrderRow(Base):
    __tablename__ = "ref_purchase_orders"

    po_number: Mapped[str] = mapped_column(String(16), primary_key=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("ref_suppliers.supplier_id"))
    po_date: Mapped[date] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(32))
    lines: Mapped[list["PurchaseOrderLineRow"]] = relationship(cascade="all, delete-orphan")


class PurchaseOrderLineRow(Base):
    __tablename__ = "ref_po_lines"

    po_number: Mapped[str] = mapped_column(
        ForeignKey("ref_purchase_orders.po_number"), primary_key=True
    )
    line_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_code: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(String(512))
    quantity_ordered: Mapped[Decimal] = mapped_column(MONEY)
    unit_price: Mapped[Decimal] = mapped_column(MONEY)


class GoodsReceivedNoteRow(Base):
    __tablename__ = "ref_grns"

    grn_number: Mapped[str] = mapped_column(String(64), primary_key=True)
    po_number: Mapped[str] = mapped_column(ForeignKey("ref_purchase_orders.po_number"))
    received_date: Mapped[date] = mapped_column(Date)
    lines: Mapped[list["GoodsReceivedNoteLineRow"]] = relationship(cascade="all, delete-orphan")


class GoodsReceivedNoteLineRow(Base):
    __tablename__ = "ref_grn_lines"
    __table_args__ = (UniqueConstraint("grn_number", "po_line_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    grn_number: Mapped[str] = mapped_column(ForeignKey("ref_grns.grn_number"))
    po_line_number: Mapped[int] = mapped_column(Integer)
    quantity_received: Mapped[Decimal] = mapped_column(MONEY)


class ReferenceSourceRow(Base):
    """Which version (file hash) of each reference file is loaded, and when.

    The audit trail can point at the sha256 that was in force for a decision.
    """

    __tablename__ = "ref_sources"

    file_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    record_count: Mapped[int] = mapped_column(Integer)
    loaded_at: Mapped[datetime] = mapped_column(DateTime)
