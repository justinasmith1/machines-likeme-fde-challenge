"""Identity injected by Azure Container Apps authentication ("Easy Auth").

When the frontend's container app requires Microsoft Entra ID sign-in, Azure puts the
signed-in person in the X-MS-CLIENT-PRINCIPAL request header and does not let a request
from outside set that header. The frontend's proxy forwards it to this API.

The header is base64 of a JSON document:

    {"auth_typ": "aad", "name_typ": "...", "role_typ": "...",
     "claims": [{"typ": "preferred_username", "val": "ana@contoso.example"},
                {"typ": "roles", "val": "AP_ANALYST"}, ...]}

It is NOT signed. This API trusts the channel it arrives on, not the header itself: the
API is reachable only from inside the Container Apps environment, where the frontend is
the only other app (DL-82).
"""

import base64
import binascii
import json
from typing import Any

from app.domain.enums import Role
from app.services.common import User

PRINCIPAL_HEADER = "X-MS-CLIENT-PRINCIPAL"

# With several roles, the one with the most privilege applies (DL-83).
ROLE_PRIORITY = (Role.AP_MANAGER, Role.AP_ANALYST, Role.INTAKE)

_ROLE_CLAIMS = {"roles", "http://schemas.microsoft.com/ws/2008/06/identity/claims/role"}
# The sign-in name first: it is what a person recognises in the audit trail (DL-84).
# The display name and the opaque identifier are fallbacks.
_USERNAME_CLAIMS = (
    "preferred_username",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/upn",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
    "email",
    "name",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/nameidentifier",
    "nameidentifier",
)


class InvalidPrincipal(Exception):
    """The header is missing, cannot be read, or names nobody this application knows."""


def _decode(header_value: str) -> dict[str, Any]:
    text = header_value.strip()
    try:
        raw = base64.b64decode(text + "=" * (-len(text) % 4), validate=True)
        document = json.loads(raw.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise InvalidPrincipal("The identity sent by the platform could not be read.") from exc
    if not isinstance(document, dict):
        raise InvalidPrincipal("The identity sent by the platform could not be read.")
    return document


def _claims(document: dict[str, Any]) -> list[tuple[str, str]]:
    claims = document.get("claims")
    if not isinstance(claims, list):
        return []
    pairs = []
    for claim in claims:
        if isinstance(claim, dict) and isinstance(claim.get("typ"), str) and isinstance(claim.get("val"), str):
            pairs.append((claim["typ"], claim["val"].strip()))
    return pairs


def role_from(values: list[str]) -> Role | None:
    """The role with the most privilege among the values, or None if none is ours."""
    known = {value.strip().upper() for value in values}
    for role in ROLE_PRIORITY:
        if role.value in known:
            return role
    return None


def user_from_principal(header_value: str | None) -> User:
    """The user and role in an X-MS-CLIENT-PRINCIPAL header. Raises InvalidPrincipal."""
    if not header_value or not header_value.strip():
        raise InvalidPrincipal("You are not signed in.")
    document = _decode(header_value)
    claims = _claims(document)

    role_types = set(_ROLE_CLAIMS)
    if isinstance(document.get("role_typ"), str):
        role_types.add(document["role_typ"])
    role_values = [value for typ, value in claims if typ in role_types]
    if isinstance(document.get("roles"), list):
        role_values += [value for value in document["roles"] if isinstance(value, str)]

    username_claims = list(_USERNAME_CLAIMS)
    if isinstance(document.get("name_typ"), str):
        username_claims.append(document["name_typ"])
    by_type: dict[str, str] = {}
    for typ, value in claims:
        if value:
            by_type.setdefault(typ, value)
    for key in ("name", "nameidentifier"):
        if isinstance(document.get(key), str) and document[key].strip():
            by_type.setdefault(key, document[key].strip())
    username = next((by_type[typ] for typ in username_claims if typ in by_type), None)
    if not username:
        raise InvalidPrincipal("The identity sent by the platform does not say who you are.")

    role = role_from(role_values)
    if role is None:
        raise InvalidPrincipal("Your account has no role in this application.")
    return User(username=username, role=role)
