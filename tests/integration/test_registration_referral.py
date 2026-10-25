"""Verify referral checks cannot be bypassed during public registration."""

import pytest
from fastapi import status
from pydantic import SecretStr
from sqlalchemy import func, select

from app.config.settings import Settings, get_settings
from app.managers.user import UserManager
from app.models.user import User

SIGNUP = {
    "first_name": "Demo",
    "last_name": "Guest",
    "email": "guest@example.com",
    "password": "DemoPassword123!",
}
INVITATION = "demo-invitation-2026"

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
def referral_gate(monkeypatch) -> Settings:
    """Enable the gate for one test without changing runtime configuration."""
    settings = get_settings()
    monkeypatch.setattr(settings, "referral_code_enabled", True)
    monkeypatch.setattr(settings, "referral_code", SecretStr(INVITATION))
    return settings


async def test_public_settings_only_expose_requirement(
    client, referral_gate, monkeypatch
) -> None:
    """Clients learn the requirement without receiving the configured code."""
    response = await client.get("/registration/settings/")
    assert response.json() == {"referral_code_required": True}
    assert response.headers["cache-control"] == "no-store"
    monkeypatch.setattr(referral_gate, "referral_code_enabled", False)
    response = await client.get("/registration/settings/")
    assert response.json() == {"referral_code_required": False}


async def test_preflight_has_no_account_or_email_side_effects(
    client, test_db, referral_gate, mocker
) -> None:
    """A successful standalone check does not register or email anyone."""
    email = mocker.patch("app.managers.user.EmailManager.template_send")
    response = await client.post(
        "/registration/referral-code/", json={"referral_code": INVITATION}
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"valid": True}
    assert await test_db.scalar(select(func.count()).select_from(User)) == 0
    email.assert_not_called()


@pytest.mark.parametrize(
    "code", [None, "", "   ", "wrong-code", INVITATION.upper(), "🙃"]
)
async def test_missing_or_wrong_code_cannot_create_account(
    client, test_db, referral_gate, mocker, code
) -> None:
    """Both the standalone check and direct registration reject bad codes."""
    email = mocker.patch("app.managers.user.EmailManager.template_send")
    supplied = {} if code is None else {"referral_code": code}
    for path, body in (
        ("/registration/referral-code/", supplied),
        ("/register/", {**SIGNUP, **supplied}),
    ):
        response = await client.post(path, json=body)
        assert response.status_code == status.HTTP_403_FORBIDDEN
    assert await test_db.scalar(select(func.count()).select_from(User)) == 0
    email.assert_not_called()


async def test_valid_code_creates_unverified_account(
    client, test_db, referral_gate, mocker
) -> None:
    """The referral field is checked and excluded from persisted user data."""
    email = mocker.patch("app.managers.user.EmailManager.template_send")
    response = await client.post(
        "/register/",
        json={**SIGNUP, "referral_code": f"  {INVITATION}  "},
    )
    assert response.status_code == status.HTTP_201_CREATED
    user = await UserManager.get_user_by_email(SIGNUP["email"], test_db)
    assert user is not None
    assert user.verified is False
    email.assert_called_once()


async def test_registration_rechecks_code_after_successful_preflight(
    client, test_db, referral_gate, monkeypatch, mocker
) -> None:
    """A previously accepted code cannot bypass a later code rotation."""
    email = mocker.patch("app.managers.user.EmailManager.template_send")
    checked = await client.post(
        "/registration/referral-code/", json={"referral_code": INVITATION}
    )
    assert checked.status_code == status.HTTP_200_OK
    monkeypatch.setattr(referral_gate, "referral_code", SecretStr("rotated"))
    response = await client.post(
        "/register/", json={**SIGNUP, "referral_code": INVITATION}
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert await test_db.scalar(select(func.count()).select_from(User)) == 0
    email.assert_not_called()


@pytest.mark.parametrize("supplied", [{}, {"referral_code": "irrelevant"}])
async def test_disabled_gate_allows_registration(
    client, test_db, referral_gate, monkeypatch, supplied
) -> None:
    """Turning the setting off restores signup without a matching code."""
    monkeypatch.setattr(referral_gate, "referral_code_enabled", False)
    monkeypatch.setattr(
        "app.managers.user.EmailManager.template_send", lambda *_: None
    )
    checked = await client.post("/registration/referral-code/", json=supplied)
    assert checked.json() == {"valid": True}
    response = await client.post("/register/", json={**SIGNUP, **supplied})
    assert response.status_code == status.HTTP_201_CREATED
    assert await UserManager.get_user_by_email(SIGNUP["email"], test_db)


async def test_admin_provisioning_is_not_gated(test_db, referral_gate) -> None:
    """The private user-manager flow used by the CLI remains available."""
    await UserManager.register(SIGNUP, test_db)
    user = await UserManager.get_user_by_email(SIGNUP["email"], test_db)
    assert user is not None
    assert user.verified is True


async def test_oversized_code_is_rejected(client, referral_gate) -> None:
    """Reject excessive input at both public entry points."""
    supplied = {"referral_code": "x" * 129}
    for path, body in (
        ("/registration/referral-code/", supplied),
        ("/register/", {**SIGNUP, **supplied}),
    ):
        response = await client.post(path, json=body)
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
