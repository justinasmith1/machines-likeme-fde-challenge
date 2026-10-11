from enum import StrEnum


class Outcome(StrEnum):
    AUTO_PAY = "AUTO_PAY"
    REJECTED = "REJECTED"
    HELD_FOR_REVIEW = "HELD_FOR_REVIEW"


# First draft: subject to the decision log (see README).
class InvoiceStatus(StrEnum):
    # Engine outcomes
    AUTO_PAY = "AUTO_PAY"
    REJECTED = "REJECTED"
    HELD_FOR_REVIEW = "HELD_FOR_REVIEW"
    # Pipeline states
    PENDING_EXTRACTION = "PENDING_EXTRACTION"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"
    NOT_AN_INVOICE = "NOT_AN_INVOICE"
    # Human actions
    APPROVED_BY_ANALYST = "APPROVED_BY_ANALYST"
    REJECTED_BY_ANALYST = "REJECTED_BY_ANALYST"
    CREDIT_NOTE_REQUESTED = "CREDIT_NOTE_REQUESTED"
    OVERRIDDEN = "OVERRIDDEN"


class Role(StrEnum):
    INTAKE = "INTAKE"
    AP_ANALYST = "AP_ANALYST"
    AP_MANAGER = "AP_MANAGER"
