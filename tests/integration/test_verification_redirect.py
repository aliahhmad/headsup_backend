"""Check browser verification redirects without changing JSON API behavior."""

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from fastapi import status

from app.config.settings import get_settings
from app.database.helpers import hash_password
from app.managers.auth import AuthManager, ResponseMessages
from app.models.user import User
from tests.helpers import get_token

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def unverified_user(test_db) -> User:
    """Create an account without sending a real verification email."""
    user = User(
        email="verify-redirect@example.com",
        first_name="Demo",
        last_name="Guest",
        password=hash_password("DemoPassword123!"),
        verified=False,
        banned=False,
    )
    test_db.add(user)
    await test_db.flush()
    await test_db.commit()
    return user


async def test_browser_returns_to_frontend_after_verification(
    client, test_db, unverified_user, monkeypatch
) -> None:
    """Verify first, then redirect without putting the token in the new URL."""
    monkeypatch.setattr(
        get_settings(), "frontend_url", "https://app.example.com/"
    )
    token = AuthManager.encode_verify_token(unverified_user)
    response = await client.get(
        "/verify/", params={"code": token}, headers={"Accept": "text/html"}
    )
    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == (
        "https://app.example.com/login?verified=1"
    )
    assert token not in response.headers["location"]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    await test_db.refresh(unverified_user)
    assert unverified_user.verified is True


@pytest.mark.parametrize("frontend", [None, "https://app.example.com"])
async def test_json_clients_keep_success_response(
    client, unverified_user, monkeypatch, frontend
) -> None:
    """The frontend's own verification requests must not receive redirects."""
    monkeypatch.setattr(get_settings(), "frontend_url", frontend)
    response = await client.get(
        "/verify/",
        params={"code": AuthManager.encode_verify_token(unverified_user)},
        headers={"Accept": "application/json"},
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"detail": ResponseMessages.VERIFICATION_SUCCESS}
    assert "location" not in response.headers


async def test_browser_without_frontend_keeps_api_response(
    client, unverified_user, monkeypatch
) -> None:
    """Standalone backend installations still have a usable verify endpoint."""
    monkeypatch.setattr(get_settings(), "frontend_url", None)
    response = await client.get(
        "/verify/",
        params={"code": AuthManager.encode_verify_token(unverified_user)},
        headers={"Accept": "text/html"},
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"detail": ResponseMessages.VERIFICATION_SUCCESS}


@pytest.mark.parametrize("expired", [False, True])
async def test_bad_browser_links_return_to_app_with_error(
    client, test_db, unverified_user, monkeypatch, expired
) -> None:
    """Invalid and expired links show an app error and never verify anyone."""
    monkeypatch.setattr(
        get_settings(), "frontend_url", "https://app.example.com"
    )
    code = (
        get_token(
            sub=unverified_user.id,
            exp=datetime.now(UTC).timestamp() - 1,
            typ="verify",
        )
        if expired
        else "invalid-token"
    )
    response = await client.get(
        "/verify/", params={"code": code}, headers={"Accept": "text/html"}
    )
    assert response.status_code == status.HTTP_303_SEE_OTHER
    outcome = "expired" if expired else "invalid"
    assert response.headers["location"] == (
        f"https://app.example.com/verify?status={outcome}"
    )
    await test_db.refresh(unverified_user)
    assert unverified_user.verified is False


async def test_invalid_json_request_keeps_error_status(
    client, monkeypatch
) -> None:
    """API failures must not be disguised as successful browser redirects."""
    monkeypatch.setattr(
        get_settings(), "frontend_url", "https://app.example.com"
    )
    response = await client.get(
        "/verify/",
        params={"code": "bad"},
        headers={"Accept": "application/json"},
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json() == {"detail": ResponseMessages.INVALID_TOKEN}
    assert "location" not in response.headers
