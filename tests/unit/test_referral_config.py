"""Validate invitation configuration without exposing the code."""

import pytest
from pydantic import SecretStr, ValidationError

from app.config.settings import Settings, get_settings

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("code", ["", " ", "\t"])
def test_enabled_gate_requires_nonempty_code(code) -> None:
    """Misconfiguration must fail startup rather than open registration."""
    values = get_settings().model_dump()
    values.update(referral_code_enabled=True, referral_code=code)
    with pytest.raises(ValidationError, match="REFERRAL_CODE must be set"):
        Settings.model_validate(values)


def test_configured_code_is_normalized_and_masked() -> None:
    """Trim surrounding whitespace while preserving the secret wrapper."""
    values = get_settings().model_dump()
    values.update(referral_code_enabled=True, referral_code="  Demo-邀请  ")
    settings = Settings.model_validate(values)
    assert isinstance(settings.referral_code, SecretStr)
    assert settings.referral_code.get_secret_value() == "Demo-邀请"
    assert "Demo-邀请" not in repr(settings)


def test_disabled_gate_accepts_empty_code() -> None:
    """Public signup can be restored without a configured invitation code."""
    values = get_settings().model_dump()
    values.update(referral_code_enabled=False, referral_code="")
    settings = Settings.model_validate(values)
    assert not settings.referral_code_enabled


def test_excessive_configured_code_is_rejected() -> None:
    """Configured codes must fit the public request schema."""
    values = get_settings().model_dump()
    values.update(referral_code_enabled=True, referral_code="x" * 129)
    with pytest.raises(ValidationError, match="at most 128 characters"):
        Settings.model_validate(values)
