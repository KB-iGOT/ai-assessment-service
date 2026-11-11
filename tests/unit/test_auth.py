"""Role check on the JWT: a token holding any one of the REQUIRED_ROLE roles passes."""

import importlib
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from assessment import auth, config

DEFAULT = "AI_ASSESSMENT_CREATOR,MDO_ADMIN,MDO_LEADER"
ROLES = ("AI_ASSESSMENT_CREATOR", "MDO_ADMIN", "MDO_LEADER")


@pytest.fixture(autouse=True)
def default_roles(monkeypatch):
    """The default roles, whatever a local .env sets REQUIRED_ROLE to."""
    monkeypatch.setattr(auth, "REQUIRED_ROLE", DEFAULT)


def test_the_default_allows_the_creator_and_both_mdo_roles(monkeypatch):
    # The default in code, not whatever a local .env sets: reloading config
    # re-runs its load_dotenv, so that is switched off for the reload.
    monkeypatch.delenv("REQUIRED_ROLE", raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: False)
    try:
        assert importlib.reload(config).REQUIRED_ROLE == DEFAULT
    finally:
        monkeypatch.undo()
        importlib.reload(config)


@pytest.mark.parametrize("role", ROLES)
def test_each_allowed_role_passes_on_its_own(role):
    assert auth.check_role({"user_roles": [role]}) is True


def test_a_token_with_several_roles_passes_if_any_is_allowed():
    assert auth.check_role({"user_roles": ["PUBLIC", "MDO_LEADER"]}) is True


@pytest.mark.parametrize("payload", [
    {"user_roles": ["PUBLIC", "CONTENT_CREATOR"]},
    {"user_roles": ["mdo_admin"]},  # roles are case-sensitive
    {"user_roles": []},
    {},
])
def test_a_token_without_an_allowed_role_fails(payload):
    assert auth.check_role(payload) is False


@pytest.mark.parametrize("setting", [
    " MDO_ADMIN , MDO_LEADER ",  # spaces around the roles
    "MDO_ADMIN,,MDO_LEADER,",    # empty entries
])
def test_the_role_list_tolerates_spaces_and_empty_entries(monkeypatch, setting):
    monkeypatch.setattr(auth, "REQUIRED_ROLE", setting)
    assert auth.check_role({"user_roles": ["MDO_LEADER"]}) is True
    assert auth.check_role({"user_roles": [""]}) is False


def test_a_single_configured_role_still_works(monkeypatch):
    monkeypatch.setattr(auth, "REQUIRED_ROLE", "AI_ASSESSMENT_CREATOR")
    assert auth.check_role({"user_roles": ["AI_ASSESSMENT_CREATOR"]}) is True
    assert auth.check_role({"user_roles": ["MDO_ADMIN"]}) is False


# --------------------------------------------------------------------------
# Through validate_token, with a real signed token
# --------------------------------------------------------------------------

@pytest.fixture
def sign(monkeypatch):
    """Signs tokens with a throwaway key that validate_token will trust."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(auth.key_manager, "get_public_key", lambda kid: key.public_key())

    def make(roles):
        claims = {"sub": "f:provider:user-1", "exp": int(time.time()) + 300,
                  "iss": f"{auth.SSO_URL}realms/{auth.SSO_REALM}", "user_roles": roles}
        return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test"})
    return make


@pytest.mark.parametrize("role", ROLES)
async def test_validate_token_accepts_each_allowed_role(sign, role):
    payload = await auth.validate_token(sign(["PUBLIC", role]))
    assert role in payload["user_roles"]


async def test_validate_token_rejects_a_token_without_an_allowed_role(sign):
    with pytest.raises(HTTPException) as error:
        await auth.validate_token(sign(["PUBLIC"]))
    assert error.value.status_code == 403
    assert error.value.detail == (
        "Forbidden: one of roles 'AI_ASSESSMENT_CREATOR,MDO_ADMIN,MDO_LEADER' required"
    )
