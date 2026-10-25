"""Persist the registration policy shared by all API workers."""

from sqlalchemy import Boolean, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.db import Base


class RegistrationSettings(Base):
    """A single policy override, falling back to environment defaults."""

    __tablename__ = "registration_settings"
    __table_args__ = (CheckConstraint("id = 1", name="singleton"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    referral_code_required: Mapped[bool] = mapped_column(Boolean)
