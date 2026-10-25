"""Define Response schemas specific to the Auth system."""

from pydantic import BaseModel


class RegistrationSettingsResponse(BaseModel):
    """Public registration requirements, without exposing the referral code."""

    referral_code_required: bool


class AdminRegistrationSettingsResponse(RegistrationSettingsResponse):
    """Admin policy details without returning the private invitation code."""

    referral_code_configured: bool


class ReferralCodeResponse(BaseModel):
    """Confirm the code check succeeded; no account has been created."""

    valid: bool


class TokenResponse(BaseModel):
    """Response Schema for Register/Login routes.

    This returns a JWT token and a Refresh token.
    """

    token: str
    refresh: str


class TokenRefreshResponse(BaseModel):
    """Return a new JWT only, after a refresh request."""

    token: str


class PasswordResetResponse(BaseModel):
    """Response schema for password reset operations."""

    message: str
