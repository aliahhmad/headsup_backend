"""Define Request schemas specific to the Auth system."""

from pydantic import BaseModel, EmailStr, Field, StrictBool

from app.managers.helpers import MAX_JWT_TOKEN_LENGTH
from app.schemas.password import (
    RESET_PASSWORD_DESCRIPTION,
    BcryptPasswordStr,
)


class ReferralCodeRequest(BaseModel):
    """Check a referral code without creating an account."""

    referral_code: str | None = Field(default=None, max_length=128)


class RegistrationSettingsUpdate(BaseModel):
    """Administrator's explicit public-signup policy."""

    referral_code_required: StrictBool


class TokenRefreshRequest(BaseModel):
    """Request schema for refreshing a JWT token."""

    refresh: str = Field(
        ...,
        max_length=MAX_JWT_TOKEN_LENGTH,
        description="JWT refresh token",
    )


class ForgotPasswordRequest(BaseModel):
    """Request schema for forgot password endpoint."""

    email: EmailStr = Field(..., description="Email address of the user")


class ResetPasswordRequest(BaseModel):
    """Request schema for reset password endpoint."""

    code: str = Field(..., description="Password reset token from email")
    new_password: BcryptPasswordStr = Field(
        ...,
        min_length=8,
        description=RESET_PASSWORD_DESCRIPTION,
    )
