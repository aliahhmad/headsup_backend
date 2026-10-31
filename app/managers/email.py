"""Define the Email Manager."""

from __future__ import annotations

import asyncio
from contextlib import suppress

from datetime import datetime, timezone
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path
from typing import TYPE_CHECKING

from aiosmtplib.errors import SMTPRecipientsRefused, SMTPResponseException
from fastapi import BackgroundTasks  # noqa: TC002
from fastapi.responses import JSONResponse
from fastapi_mail import ConnectionConfig, FastMail, MessageSchema, MessageType
from fastapi_mail.connection import Connection
from fastapi_mail.fastmail import email_dispatched

from app.config.settings import get_settings
from app.logs import LogCategory, category_logger

if TYPE_CHECKING:  # pragma: no cover
    from app.schemas.email import EmailSchema, EmailTemplateSchema


class AlertEmailTransportError(Exception):
    """A safe SMTP outcome; uncertainty must never trigger automatic replay."""

    def __init__(
        self, code: str, *, retryable: bool = False, uncertain: bool = False
    ) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.uncertain = uncertain


class EmailManager:
    """Class to manage all Email operations."""

    def __init__(self, *, suppress_send: bool | None = False) -> None:
        """Initialize the EmailManager.

        Define the configuration instance.
        """
        self.conf = ConnectionConfig(
            MAIL_USERNAME=get_settings().mail_username,
            MAIL_PASSWORD=get_settings().mail_password,
            MAIL_FROM=get_settings().mail_from,
            MAIL_PORT=get_settings().mail_port,
            MAIL_SERVER=get_settings().mail_server,
            MAIL_FROM_NAME=get_settings().mail_from_name,
            MAIL_STARTTLS=get_settings().mail_starttls,
            MAIL_SSL_TLS=get_settings().mail_ssl_tls,
            USE_CREDENTIALS=get_settings().mail_use_credentials,
            VALIDATE_CERTS=get_settings().mail_validate_certs,
            TEMPLATE_FOLDER=(
                Path(__file__).parent.parent / "templates" / "email"
            ),
            SUPPRESS_SEND=1 if suppress_send else 0,
        )

    async def simple_send(self, email_data: EmailSchema) -> JSONResponse:
        """Send a plain email with a subject and message."""
        message = MessageSchema(
            subject=email_data.subject,
            recipients=email_data.recipients,
            body=email_data.body,
            subtype=MessageType.html,
        )

        try:
            fm = FastMail(self.conf)
            await fm.send_message(message)

            recipients_list = email_data.recipients
            category_logger.info(
                f"Email sent: '{email_data.subject}' to {recipients_list}",
                LogCategory.EMAIL,
            )

            return JSONResponse(
                status_code=200, content={"message": "email has been sent"}
            )
        except Exception as exc:
            category_logger.error(
                f"Failed to send email: {exc}", LogCategory.ERRORS
            )
            raise

    def background_send(
        self, backgroundtasks: BackgroundTasks, email_data: EmailSchema
    ) -> None:
        """Send an email in the background."""
        message = MessageSchema(
            subject=email_data.subject,
            recipients=email_data.recipients,
            body=email_data.body,
            subtype=MessageType.html,
        )

        fm = FastMail(self.conf)
        backgroundtasks.add_task(fm.send_message, message)

        recipients_list = email_data.recipients
        category_logger.info(
            f"Email queued for background send: '{email_data.subject}' "
            f"to {recipients_list}",
            LogCategory.EMAIL,
        )

    def template_send(
        self, backgroundtasks: BackgroundTasks, email_data: EmailTemplateSchema
    ) -> None:
        """Send an email using a Jinja Template."""
        backgroundtasks.add_task(self._send_template_message, email_data)
        recipients = ", ".join(r.email for r in email_data.recipients)
        category_logger.info(
            f"Template email queued: '{email_data.subject}' "
            f"({email_data.template_name}) to {recipients}",
            LogCategory.EMAIL,
        )

    def render_template_message(
        self, email_data: EmailTemplateSchema
    ) -> EmailMessage:
        """Build a valid MIME message with plain text before preferred HTML."""
        settings = get_settings()
        template_body = {
            "application": settings.api_title,
            "base_url": settings.base_url,
            "frontend_url": settings.frontend_url or settings.base_url,
            "year": datetime.now(timezone.utc).year,
            **email_data.body,
        }
        plain_template = str(Path(email_data.template_name).with_suffix(".txt"))
        has_plain_template = bool(
            self.conf.TEMPLATE_FOLDER
            and (self.conf.TEMPLATE_FOLDER / plain_template).is_file()
        )
        engine = self.conf.template_engine()
        html = engine.get_template(email_data.template_name).render(
            **template_body
        )
        message = EmailMessage(policy=SMTP)
        message["Subject"] = email_data.subject
        message["From"] = formataddr(
            (self.conf.MAIL_FROM_NAME or "", str(self.conf.MAIL_FROM))
        )
        message["To"] = ", ".join(
            formataddr((recipient.name, recipient.email))
            for recipient in email_data.recipients
        )
        message["Date"] = formatdate(localtime=False)
        message["Message-ID"] = make_msgid()
        if has_plain_template:
            plain = engine.get_template(plain_template).render(**template_body)
            message.set_content(plain)
            # MIME clients prefer the last supported alternative (RFC 2046).
            message.add_alternative(html, subtype="html")
        else:
            message.set_content(html, subtype="html")
        return message

    async def _send_template_message(
        self, email_data: EmailTemplateSchema
    ) -> None:
        """Deliver the rendered message using the configured SMTP connection."""
        message = self.render_template_message(email_data)
        async with Connection(self.conf) as connection:
            if not self.conf.SUPPRESS_SEND:
                await connection.session.send_message(message)
        email_dispatched.send(message)

    async def send_alert_message(self, message: EmailMessage) -> None:
        """Send one recipient and distinguish explicit refusal from lost confirmation."""
        if self.conf.SUPPRESS_SEND:
            return
        connection = Connection(self.conf)
        try:
            await asyncio.wait_for(connection.__aenter__(), timeout=30)
        except asyncio.CancelledError:
            if hasattr(connection, "session"):
                connection.session.close()
            raise
        except Exception:
            if hasattr(connection, "session"):
                connection.session.close()
            # No SMTP DATA was attempted; retrying cannot duplicate this email.
            raise AlertEmailTransportError(
                "smtp_connection_failed", retryable=True
            ) from None
        try:
            try:
                await asyncio.wait_for(
                    connection.session.send_message(message), timeout=30
                )
            except SMTPRecipientsRefused as exc:
                retryable = bool(exc.recipients) and all(
                    400 <= refusal.code < 500 for refusal in exc.recipients
                )
                raise AlertEmailTransportError(
                    "smtp_recipient_refused", retryable=retryable
                ) from None
            except SMTPResponseException as exc:
                raise AlertEmailTransportError(
                    "smtp_message_refused", retryable=400 <= exc.code < 500
                ) from None
            except asyncio.CancelledError:
                raise
            except Exception:
                # SMTP could have accepted DATA before the connection was lost.
                raise AlertEmailTransportError(
                    "smtp_confirmation_lost", uncertain=True
                ) from None
        finally:
            # A failed QUIT after acceptance must not turn success into a retry.
            with suppress(Exception):
                await asyncio.wait_for(
                    connection.__aexit__(None, None, None), timeout=5
                )
            connection.session.close()
