from dataclasses import dataclass, field

from app.domain.enums import Outcome
from app.domain.models import ExtractedInvoice, GoodsReceivedNote, PurchaseOrder, Supplier
from app.rules.parameters import RuleParameters


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    # Plain accounting language, one entry per triggered rule.
    reasons: list[str] = field(default_factory=list)


def evaluate(
    invoice: ExtractedInvoice,
    supplier: Supplier | None,
    purchase_order: PurchaseOrder | None,
    grns: list[GoodsReceivedNote],
    params: RuleParameters,
) -> Decision:
    """Assign exactly one outcome to an invoice. Pure and deterministic: no I/O, no LLM.

    Precedence:
      1. Rejected: if any rejection rule triggers, the result is Rejected.
      2. Held for Review: otherwise, if any hold rule triggers.
      3. Auto-Pay: the default when nothing above triggers.

    Reasons from all triggered rules of the winning outcome are returned.
    Not implemented yet (Phase 2).
    """
    raise NotImplementedError("rules engine is implemented in Phase 2")
