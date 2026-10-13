from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal

from app.config import Settings

ALL_ASSUMPTION_RULES: frozenset[str] = frozenset(
    {"A1", "A2", "A3", "A4", "A5", "A8", "A10"}
)


# NOTE: the AP Manager will change these at runtime from the database. Persistence
# (storage, versioning, and the per-evaluation snapshot required by DL-11) comes in a
# later phase. Until then, values come from Settings or the defaults below.
@dataclass(frozen=True)
class RuleParameters:
    price_tolerance_pct: Decimal = Decimal("0.02")  # DL-02, inclusive
    analyst_approval_limit: Decimal = Decimal("5000")
    rounding_tolerance: Decimal = Decimal("0.01")  # DL-17
    # Assumption rules that are active. Spec rules, and the fixed rules A6, A7 and A9
    # (DL-18, DL-35, DL-78), are always active and are not in this list.
    enabled_assumption_rules: frozenset[str] = ALL_ASSUMPTION_RULES
    version: int | None = None
    changed_by: str | None = None
    changed_at: datetime | None = None

    def snapshot(self) -> "RuleParameters":
        """Copy stored with each evaluation (DL-11). Immutable: frozen fields and frozenset."""
        return replace(self)

    @classmethod
    def from_settings(cls, settings: Settings) -> "RuleParameters":
        return cls(
            price_tolerance_pct=settings.price_tolerance_pct,
            analyst_approval_limit=settings.analyst_approval_limit,
        )
