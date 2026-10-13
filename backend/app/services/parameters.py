"""Versioned rule parameters (DL-11).

The current parameters are the highest version in rule_parameters. A change never edits a
row: it adds a version. Evaluations keep a snapshot of the parameters they used, so a
change only affects evaluations made after it.
"""

from datetime import datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.invoice_tables import RuleParametersRow
from app.domain.enums import Role
from app.rules.parameters import ALL_ASSUMPTION_RULES, RuleParameters
from app.services.common import User, record_event
from app.services.errors import PermissionDenied, ValidationError

_DECIMAL_FIELDS = ("price_tolerance_pct", "analyst_approval_limit", "rounding_tolerance")
_EDITABLE = (*_DECIMAL_FIELDS, "enabled_assumption_rules")


def snapshot_to_json(parameters: RuleParameters) -> dict:
    return {
        "price_tolerance_pct": str(parameters.price_tolerance_pct),
        "analyst_approval_limit": str(parameters.analyst_approval_limit),
        "rounding_tolerance": str(parameters.rounding_tolerance),
        "enabled_assumption_rules": sorted(parameters.enabled_assumption_rules),
        "version": parameters.version,
        "changed_by": parameters.changed_by,
        "changed_at": parameters.changed_at.isoformat() if parameters.changed_at else None,
    }


def _from_row(row: RuleParametersRow) -> RuleParameters:
    return RuleParameters(
        price_tolerance_pct=row.price_tolerance_pct,
        analyst_approval_limit=row.analyst_approval_limit,
        rounding_tolerance=row.rounding_tolerance,
        enabled_assumption_rules=frozenset(row.enabled_assumption_rules),
        version=row.version,
        changed_by=row.changed_by,
        changed_at=row.changed_at,
    )


def _latest_row(session: Session) -> RuleParametersRow | None:
    return session.scalars(
        select(RuleParametersRow).order_by(RuleParametersRow.version.desc()).limit(1)
    ).first()


def current_parameters(session: Session, settings: Settings, now: datetime) -> RuleParameters:
    """Parameters in force. The first call seeds version 1 from Settings."""
    row = _latest_row(session)
    if row is None:
        seed = RuleParameters.from_settings(settings)
        row = RuleParametersRow(
            version=1,
            price_tolerance_pct=seed.price_tolerance_pct,
            analyst_approval_limit=seed.analyst_approval_limit,
            rounding_tolerance=seed.rounding_tolerance,
            enabled_assumption_rules=sorted(seed.enabled_assumption_rules),
            changed_by="system",
            changed_at=now,
            comment="Initial values from configuration.",
        )
        session.add(row)
        record_event(session, "PARAMETERS_SEEDED", timestamp=now, details={"version": 1})
        session.flush()
    return _from_row(row)


def update_parameters(
    session: Session, settings: Settings, values: dict, comment: str, user: User, now: datetime
) -> RuleParameters:
    """Add a new parameter version. AP Manager only. Affects new evaluations only."""
    if user.role is not Role.AP_MANAGER:
        raise PermissionDenied("Only the AP Manager can change the rule parameters.")
    if not comment or not comment.strip():
        raise ValidationError("A comment explaining the change is required.")
    unknown = sorted(set(values) - set(_EDITABLE))
    if unknown:
        raise ValidationError(f"Unknown parameter(s): {', '.join(unknown)}.")
    if not values:
        raise ValidationError("No parameter was given to change.")

    current = current_parameters(session, settings, now)
    new = {
        "price_tolerance_pct": current.price_tolerance_pct,
        "analyst_approval_limit": current.analyst_approval_limit,
        "rounding_tolerance": current.rounding_tolerance,
        "enabled_assumption_rules": sorted(current.enabled_assumption_rules),
    }
    for name in _DECIMAL_FIELDS:
        if name in values:
            try:
                number = Decimal(str(values[name]))
            except InvalidOperation:
                raise ValidationError(f"{name} must be a number.") from None
            if not number.is_finite() or number < 0:
                raise ValidationError(f"{name} cannot be negative.")
            new[name] = number
    if "enabled_assumption_rules" in values:
        rules = values["enabled_assumption_rules"]
        if isinstance(rules, str) or not all(isinstance(r, str) for r in rules):
            raise ValidationError("enabled_assumption_rules must be a list of rule codes.")
        invalid = sorted(set(rules) - ALL_ASSUMPTION_RULES)
        if invalid:
            raise ValidationError(
                f"Not assumption rules: {', '.join(invalid)}. Spec rules cannot be switched."
            )
        new["enabled_assumption_rules"] = sorted(set(rules))

    row = RuleParametersRow(
        version=(current.version or 0) + 1,
        changed_by=user.username,
        changed_at=now,
        comment=comment.strip(),
        **new,
    )
    session.add(row)
    record_event(
        session,
        "PARAMETERS_UPDATED",
        timestamp=now,
        actor=user.username,
        role=user.role,
        details={
            "version": row.version,
            "previous_version": current.version,
            "changed": sorted(values),
            "comment": comment.strip(),
        },
    )
    session.flush()
    return _from_row(row)
