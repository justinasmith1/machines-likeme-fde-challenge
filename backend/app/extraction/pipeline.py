"""From an ExtractionResult to a final invoice status.

Only a document classified as an invoice reaches the rules engine. A non-invoice ends as
NOT_AN_INVOICE and a failed extraction as EXTRACTION_FAILED, both without an engine call.

The MatchingContext is supplied by the caller through build_context, because resolving
suppliers, purchase orders and line mapping belongs to the matching layer (phase 4).
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from app.domain.enums import InvoiceStatus
from app.extraction.mapper import MappedInvoice, to_engine_input
from app.extraction.service import ExtractionResult, ExtractionStatus
from app.rules.engine import Decision, MatchingContext, evaluate
from app.rules.parameters import RuleParameters


@dataclass(frozen=True)
class PipelineOutcome:
    status: InvoiceStatus
    reason: str | None = None  # shown to the intake user when there is no engine decision
    decision: Decision | None = None  # None when the engine did not run
    mapped: MappedInvoice | None = None
    flags: list[str] = field(default_factory=list)


def decide(
    result: ExtractionResult,
    build_context: Callable[[MappedInvoice], MatchingContext],
    parameters: RuleParameters,
) -> PipelineOutcome:
    if result.status is ExtractionStatus.EXTRACTION_FAILED:
        return PipelineOutcome(
            status=InvoiceStatus.EXTRACTION_FAILED,
            reason="; ".join(result.errors) or "The document could not be read.",
        )
    if result.status is ExtractionStatus.NOT_AN_INVOICE:
        detail = f" ({result.classification_reason})" if result.classification_reason else ""
        return PipelineOutcome(
            status=InvoiceStatus.NOT_AN_INVOICE,
            reason=f"Received, not an invoice: {result.document_type}{detail}",
        )

    mapped = to_engine_input(result.extraction)
    decision = evaluate(mapped.invoice, build_context(mapped), parameters)
    return PipelineOutcome(
        status=InvoiceStatus[decision.outcome.name],
        decision=decision,
        mapped=mapped,
        flags=list(mapped.flags),
    )
