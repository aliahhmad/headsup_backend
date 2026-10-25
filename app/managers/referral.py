"""Validate the optional referral-code gate before public registration."""

from secrets import compare_digest

from fastapi import HTTPException, status
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings, unwrap_secret
from app.models.registration import RegistrationSettings


async def referral_code_required(session: AsyncSession) -> bool:
    """Read the saved policy, using the environment until an admin saves it."""
    policy = await session.get(RegistrationSettings, 1)
    if policy is not None:
        return policy.referral_code_required
    return get_settings().referral_code_enabled


async def save_referral_requirement(
    session: AsyncSession, *, required: bool
) -> None:
    """Persist a policy override without modifying or exposing the code."""
    if required and not unwrap_secret(get_settings().referral_code):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "Configure a referral code on the server before "
                "enabling referral-only signup."
            ),
        )
    statement = insert(RegistrationSettings).values(
        id=1, referral_code_required=required
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[RegistrationSettings.id],
            set_={"referral_code_required": required},
        )
    )


async def validate_referral_code(
    code: str | None, session: AsyncSession
) -> None:
    """Reject missing or invalid codes when the signup gate is enabled."""
    settings = get_settings()
    if not await referral_code_required(session):
        return

    expected = unwrap_secret(settings.referral_code)
    supplied = (code or "").strip()
    if not expected or not compare_digest(
        supplied.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="A valid referral code is required to join this demo.",
        )
