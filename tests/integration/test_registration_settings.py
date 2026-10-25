"""Verify administrator-controlled signup policy and public enforcement."""

import pytest
import pytest_asyncio
from fastapi import status
from pydantic import SecretStr
from sqlalchemy import update

from app.config.settings import get_settings
from app.database.helpers import hash_password
from app.managers.auth import AuthManager
from app.models.enums import RoleType
from app.models.registration import RegistrationSettings
from app.models.user import User

ADMIN_PATH = "/admin/registration/settings/"
PUBLIC_PATH = "/registration/settings/"
INVITATION = "demo-settings-invitation"

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def admin_headers(test_db) -> dict[str, str]:
    """Create an authenticated admin without changing registration policy."""
    admin = User(
        first_name="Demo",
        last_name="Administrator",
        email="admin@example.com",
        password=hash_password("DemoPassword123!"),
        role=RoleType.admin,
        verified=True,
        banned=False,
    )
    test_db.add(admin)
    await test_db.flush()
    await test_db.commit()
    return {"Authorization": f"Bearer {AuthManager.encode_token(admin)}"}


@pytest.fixture
def configured_code(monkeypatch) -> None:
    """Set a private server code and an initially disabled signup gate."""
    monkeypatch.setattr(get_settings(), "referral_code_enabled", False)
    monkeypatch.setattr(get_settings(), "referral_code", SecretStr(INVITATION))


async def test_admin_reads_policy_without_exposing_code(
    client, admin_headers, configured_code
) -> None:
    """The administrator sees availability, not the private code itself."""
    response = await client.get(ADMIN_PATH, headers=admin_headers)
    assert response.status_code == status.HTTP_200_OK
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "referral_code_required": False,
        "referral_code_configured": True,
    }
    assert INVITATION not in response.text


async def test_saved_policy_is_shared_and_overrides_environment(
    client, admin_headers, test_sessionmaker, configured_code, monkeypatch
) -> None:
    """Saved changes survive new sessions without mutating worker settings."""
    response = await client.patch(
        ADMIN_PATH,
        headers=admin_headers,
        json={"referral_code_required": True},
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["referral_code_required"] is True
    assert get_settings().referral_code_enabled is False
    async with test_sessionmaker() as fresh_session:
        saved = await fresh_session.get(RegistrationSettings, 1)
        assert saved is not None
        assert saved.referral_code_required is True
    response = await client.get(PUBLIC_PATH)
    assert response.json() == {"referral_code_required": True}
    # A process loading a different environment default still uses the row.
    monkeypatch.setattr(get_settings(), "referral_code_enabled", True)
    response = await client.patch(
        ADMIN_PATH,
        headers=admin_headers,
        json={"referral_code_required": False},
    )
    assert response.json()["referral_code_required"] is False
    response = await client.get(PUBLIC_PATH)
    assert response.json() == {"referral_code_required": False}


async def test_switch_controls_preflight_and_direct_registration(
    client, admin_headers, configured_code, mocker
) -> None:
    """Both public entry points enforce the saved policy immediately."""
    email = mocker.patch("app.managers.user.EmailManager.template_send")
    signup = {
        "first_name": "Demo",
        "last_name": "Guest",
        "email": "guest@example.com",
        "password": "DemoPassword123!",
    }
    await client.patch(
        ADMIN_PATH,
        headers=admin_headers,
        json={"referral_code_required": True},
    )
    for path, body in (
        ("/registration/referral-code/", {}),
        ("/register/", signup),
    ):
        response = await client.post(path, json=body)
        assert response.status_code == status.HTTP_403_FORBIDDEN
    email.assert_not_called()
    checked = await client.post(
        "/registration/referral-code/", json={"referral_code": INVITATION}
    )
    assert checked.status_code == status.HTTP_200_OK
    await client.patch(
        ADMIN_PATH,
        headers=admin_headers,
        json={"referral_code_required": False},
    )
    checked = await client.post("/registration/referral-code/", json={})
    assert checked.json() == {"valid": True}
    registered = await client.post("/register/", json=signup)
    assert registered.status_code == status.HTTP_201_CREATED
    email.assert_called_once()


async def test_enabling_without_configured_code_is_rejected(
    client, admin_headers, monkeypatch, test_db
) -> None:
    """A failed save cannot lock signup behind a missing invitation code."""
    monkeypatch.setattr(get_settings(), "referral_code", SecretStr(""))
    response = await client.patch(
        ADMIN_PATH,
        headers=admin_headers,
        json={"referral_code_required": True},
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert await test_db.get(RegistrationSettings, 1) is None


@pytest.mark.parametrize("method", ["get", "patch"])
async def test_anonymous_users_cannot_manage_signup(client, method) -> None:
    """Management requires authentication, including the read endpoint."""
    kwargs = (
        {"json": {"referral_code_required": False}} if method == "patch" else {}
    )
    response = await getattr(client, method)(ADMIN_PATH, **kwargs)
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.parametrize("method", ["get", "patch"])
async def test_regular_users_cannot_manage_signup(
    client, admin_headers, test_db, method
) -> None:
    """A valid regular-user session cannot read or change the admin policy."""
    await test_db.execute(update(User).values(role=RoleType.user))
    await test_db.commit()
    kwargs = (
        {"json": {"referral_code_required": False}} if method == "patch" else {}
    )
    response = await getattr(client, method)(
        ADMIN_PATH, headers=admin_headers, **kwargs
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert await test_db.get(RegistrationSettings, 1) is None


@pytest.mark.parametrize("value", [None, "false", 0, 1])
async def test_policy_requires_explicit_boolean(
    client, admin_headers, value
) -> None:
    """Ambiguous values must not inadvertently open public signup."""
    response = await client.patch(
        ADMIN_PATH,
        headers=admin_headers,
        json={"referral_code_required": value},
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
