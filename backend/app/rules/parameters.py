from dataclasses import dataclass
from decimal import Decimal

from app.config import Settings


# NOTE: the AP Manager will change these at runtime from the database. Persistence
# design (storage, versioning, per-evaluation snapshot per DL-11) comes in a later phase.
# Until then, values come from Settings.
@dataclass(frozen=True)
class RuleParameters:
    price_tolerance_pct: Decimal = Decimal("0.02")
    analyst_approval_limit: Decimal = Decimal("5000")

    @classmethod
    def from_settings(cls, settings: Settings) -> "RuleParameters":
        return cls(
            price_tolerance_pct=settings.price_tolerance_pct,
            analyst_approval_limit=settings.analyst_approval_limit,
        )
