"""APP_ENV=entra: the caller is whoever Azure Container Apps authentication says it is."""

import base64
import json

import pytest
from fastapi.testclient import TestClient

from app.api.identity import InvalidPrincipal, role_from, user_from_principal
from app.config import Settings
from app.db.session import init_db, make_engine
from app.domain.enums import Role
from app.main import create_app
from app.storage.files import LocalFileStore
from db_builders import StubExtractor

HEADER = "X-MS-CLIENT-PRINCIPAL"
ROLE_URI = "http://schemas.microsoft.com/ws/2008/06/identity/claims/role"
NAME_ID_URI = "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/nameidentifier"


def principal(*roles: str, username: str | None = "ana@contoso.example", role_type: str = "roles", **extra) -> str:
    """An X-MS-CLIENT-PRINCIPAL value as the platform builds it. Fictional people only."""
    claims = [{"typ": "aud", "val": "00000000-0000-0000-0000-000000000000"}]
    if username is not None:
        claims.append({"typ": "preferred_username", "val": username})
    claims += [{"typ": role_type, "val": role} for role in roles]
    document = {"auth_typ": "aad", "name_typ": "name", "role_typ": ROLE_URI, "claims": claims, **extra}
    return base64.b64encode(json.dumps(document).encode()).decode()


def encode(document) -> str:
    return base64.b64encode(json.dumps(document).encode()).decode()


def make_client(tmp_path, app_env: str) -> TestClient:
    engine = make_engine("sqlite://")
    init_db(engine)
    app = create_app(
        Settings(_env_file=None, app_env=app_env),
        engine=engine,
        file_store=LocalFileStore(tmp_path / "uploads"),
        extractor=StubExtractor(),
    )
    return TestClient(app)


@pytest.fixture
def client(tmp_path):
    with make_client(tmp_path, "entra") as client:
        yield client


def _refused(response, text: str | None = None) -> None:
    assert response.status_code == 401, response.text
    error = response.json()["error"]
    assert error["code"] == "UNAUTHENTICATED"
    if text:
        assert text in error["message"]


# ---------------------------------------------------------------- through the API


@pytest.mark.parametrize("role", ["INTAKE", "AP_ANALYST", "AP_MANAGER"])
def test_a_valid_principal_is_the_caller(client, role):
    response = client.get("/api/me", headers={HEADER: principal(role)})
    assert response.status_code == 200, response.text
    assert response.json()["username"] == "ana@contoso.example"
    assert response.json()["role"] == role


def test_without_the_header_the_request_is_refused(client):
    _refused(client.get("/api/me"), "not signed in")
    _refused(client.get("/api/me", headers={HEADER: "   "}), "not signed in")


@pytest.mark.parametrize(
    "value",
    [
        "this is not base64 !!!",
        base64.b64encode(b"not json at all").decode(),
        base64.b64encode(b"\xff\xfe\x00").decode(),
        encode(["a", "list"]),
        encode("a string"),
    ],
)
def test_a_header_that_cannot_be_read_is_refused(client, value):
    _refused(client.get("/api/me", headers={HEADER: value}), "could not be read")


def test_a_principal_without_roles_is_refused(client):
    _refused(client.get("/api/me", headers={HEADER: principal()}), "no role")
    _refused(client.get("/api/me", headers={HEADER: encode({"claims": []})}))
    _refused(client.get("/api/me", headers={HEADER: encode({})}))


def test_a_role_this_application_does_not_know_is_refused(client):
    _refused(client.get("/api/me", headers={HEADER: principal("Global Administrator", "ADMIN")}), "no role")


def test_with_several_roles_the_one_with_most_privilege_applies(client):
    for roles, expected in [
        (("INTAKE", "AP_MANAGER", "AP_ANALYST"), "AP_MANAGER"),
        (("INTAKE", "AP_ANALYST"), "AP_ANALYST"),
        (("AP_ANALYST", "INTAKE"), "AP_ANALYST"),
        (("SOMETHING_ELSE", "INTAKE"), "INTAKE"),
    ]:
        response = client.get("/api/me", headers={HEADER: principal(*roles)})
        assert response.json()["role"] == expected, roles


def test_development_headers_are_ignored_completely(client):
    dev = {"X-Dev-User": "mallory", "X-Dev-Role": "AP_MANAGER"}
    # Alone, they authenticate nobody.
    _refused(client.get("/api/me", headers=dev), "not signed in")
    # Next to a real principal, they change nothing.
    response = client.get("/api/me", headers={**dev, HEADER: principal("INTAKE")})
    assert response.json() == {**response.json(), "username": "ana@contoso.example", "role": "INTAKE"}


def test_permissions_follow_the_role_in_the_principal(client):
    intake, manager = {HEADER: principal("INTAKE")}, {HEADER: principal("AP_MANAGER", username="max@contoso.example")}
    assert client.get("/api/parameters", headers=intake).status_code == 403
    assert client.get("/api/review-queue", headers=intake).status_code == 403
    assert client.get("/api/parameters", headers=manager).status_code == 200
    assert client.get("/api/review-queue", headers=manager).status_code == 200


def test_the_person_in_the_principal_is_who_the_audit_trail_names(client):
    manager = {HEADER: principal("AP_MANAGER", username="max@contoso.example")}
    saved = client.put(
        "/api/parameters", json={"values": {"price_tolerance_pct": "0.03"}, "comment": "Agreed with AP."}, headers=manager
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["changed_by"] == "max@contoso.example"


# ---------------------------------------------------------------- the other modes


def test_in_development_the_principal_header_authenticates_nobody(tmp_path):
    with make_client(tmp_path, "development") as client:
        _refused(client.get("/api/me", headers={HEADER: principal("AP_MANAGER")}))
        ok = client.get("/api/me", headers={"X-Dev-User": "ana", "X-Dev-Role": "AP_ANALYST"})
        assert ok.status_code == 200 and ok.json()["username"] == "ana"


@pytest.mark.parametrize("app_env", ["production", "staging", "ENTRA", ""])
def test_any_other_mode_refuses_everything(tmp_path, app_env):
    with make_client(tmp_path, app_env) as client:
        _refused(client.get("/api/me", headers={HEADER: principal("AP_MANAGER")}), "not configured")
        _refused(client.get("/api/me", headers={"X-Dev-User": "ana", "X-Dev-Role": "AP_MANAGER"}), "not configured")


def test_the_default_mode_is_closed():
    assert Settings(_env_file=None).app_env == "production"


# ---------------------------------------------------------------- reading the header


def test_roles_are_read_from_either_claim_type_and_from_a_plain_list():
    assert user_from_principal(principal("AP_ANALYST", role_type=ROLE_URI)).role is Role.AP_ANALYST
    assert user_from_principal(principal("AP_ANALYST", role_type="roles")).role is Role.AP_ANALYST
    custom = principal("INTAKE", role_type="custom/role", role_typ="custom/role")
    assert user_from_principal(custom).role is Role.INTAKE
    plain = encode({"name": "ana@contoso.example", "roles": ["INTAKE", "AP_MANAGER"]})
    assert user_from_principal(plain).role is Role.AP_MANAGER


def test_the_sign_in_name_is_preferred_then_the_display_name_then_the_identifier():
    def user(claims, **extra):
        document = {"claims": [{"typ": "roles", "val": "INTAKE"}, *claims], **extra}
        return user_from_principal(encode(document)).username

    name = {"typ": "name", "val": "Ana Analyst"}
    identifier = {"typ": NAME_ID_URI, "val": "opaque-id-123"}
    assert user([identifier, name, {"typ": "preferred_username", "val": "ana@contoso.example"}]) == "ana@contoso.example"
    assert user([identifier, name]) == "Ana Analyst"
    assert user([identifier]) == "opaque-id-123"
    assert user([], name="Top Level Name") == "Top Level Name"
    assert user([], nameidentifier="top-level-id") == "top-level-id"
    assert user([{"typ": "custom/name", "val": "By name_typ"}], name_typ="custom/name") == "By name_typ"


def test_a_principal_that_names_nobody_is_refused():
    with pytest.raises(InvalidPrincipal, match="does not say who you are"):
        user_from_principal(principal("AP_MANAGER", username=None))
    with pytest.raises(InvalidPrincipal, match="does not say who you are"):
        user_from_principal(principal("AP_MANAGER", username="   "))


def test_malformed_claims_are_skipped_not_fatal():
    document = {
        "claims": [
            "not a claim",
            {"typ": "roles"},
            {"typ": 7, "val": "AP_MANAGER"},
            {"typ": "roles", "val": None},
            {"typ": "preferred_username", "val": "ana@contoso.example"},
            {"typ": "roles", "val": " ap_analyst "},
        ]
    }
    user = user_from_principal(encode(document))
    assert (user.username, user.role) == ("ana@contoso.example", Role.AP_ANALYST)


def test_base64_without_padding_is_accepted():
    value = principal("INTAKE").rstrip("=")
    assert user_from_principal(value).role is Role.INTAKE


def test_role_priority():
    assert role_from([]) is None
    assert role_from(["nothing", ""]) is None
    assert role_from(["INTAKE"]) is Role.INTAKE
    assert role_from(["intake", "AP_ANALYST"]) is Role.AP_ANALYST
    assert role_from(["AP_ANALYST", "AP_MANAGER", "INTAKE"]) is Role.AP_MANAGER
