"""Administrator-only controls for public account registration."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings, unwrap_secret
from app.database.db import get_database
from app.managers.auth import is_admin
from app.managers.referral import (
    referral_code_required,
    save_referral_requirement,
)
from app.managers.security import get_current_user
from app.schemas.request.auth import RegistrationSettingsUpdate
from app.schemas.response.auth import AdminRegistrationSettingsResponse

router = APIRouter(
    tags=["Administration"],
    prefix="/admin/registration",
    dependencies=[Depends(get_current_user), Depends(is_admin)],
)


@router.get("/settings/", response_model=AdminRegistrationSettingsResponse)
async def get_registration_settings(
    response: Response,
    session: Annotated[AsyncSession, Depends(get_database)],
) -> dict[str, bool]:
    """Read the effective registration policy and code configuration status."""
    response.headers["Cache-Control"] = "no-store"
    return {
        "referral_code_required": await referral_code_required(session),
        "referral_code_configured": bool(
            unwrap_secret(get_settings().referral_code)
        ),
    }


@router.patch("/settings/", response_model=AdminRegistrationSettingsResponse)
async def update_registration_settings(
    data: RegistrationSettingsUpdate,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_database)],
) -> dict[str, bool]:
    """Save an immediate policy override shared across workers and restarts."""
    await save_referral_requirement(
        session, required=data.referral_code_required
    )
    response.headers["Cache-Control"] = "no-store"
    return {
        "referral_code_required": data.referral_code_required,
        "referral_code_configured": bool(
            unwrap_secret(get_settings().referral_code)
        ),
    }
