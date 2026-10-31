"""Email rendering and SMTP outcome classification without external delivery."""

from email import policy
from email.parser import BytesParser
from uuid import uuid4

import pytest
from aiosmtplib.errors import (
    SMTPDataError,
    SMTPRecipientRefused,
    SMTPRecipientsRefused,
)

from app.config.settings import get_settings
from app.managers import alert_email as delivery
from app.managers.email import AlertEmailTransportError

pytestmark = pytest.mark.unit


@pytest.fixture
def context(monkeypatch):
    settings = get_settings().model_copy(
        update={"frontend_url": "https://headsup.example.test"}
    )
    monkeypatch.setattr(delivery, "get_settings", lambda: settings)
    return {
        "delivery_id": str(uuid4()),
        "recipient": "reader@example.com",
        "name": "Alex & Morgan",
        "monitor_id": str(uuid4()),
        "alert_id": str(uuid4()),
        "monitor_name": "F1 Race Watch",
        "monitor_objective": "Track confirmed race updates",
        "alert_title": "Example race update",
        "alert_body": "A confirmed change.\n\nRead the official source.",
        "alert_paragraphs": [
            "A confirmed change.",
            "Read the official source.",
        ],
        "published_at": "October 31, 2026 at 18:00 UTC",
        "preview": "A confirmed change.",
        "sources": [
            {
                "title": "Example source",
                "url": "https://example.com/race?event=1&view=full",
            }
        ],
    }


def test_multipart_content_and_exact_deep_links(email_manager, context):
    message = delivery.render_alert_email(context, email_manager)
    parsed = BytesParser(policy=policy.default).parsebytes(message.as_bytes())
    assert [part.get_content_type() for part in parsed.iter_parts()] == [
        "text/plain",
        "text/html",
    ]
    assert all(not part.defects for part in parsed.walk())
    html = parsed.get_body(preferencelist=("html",)).get_content()
    plain = parsed.get_body(preferencelist=("plain",)).get_content()
    assert 'role="presentation"' in html
    assert 'style="' in html
    assert "Hi Alex &amp; Morgan," in html
    assert "Hi Alex & Morgan," in plain
    assert "https://example.com/race?event=1&amp;view=full" in html
    assert "https://example.com/race?event=1&view=full" in plain
    assert str(parsed["Message-ID"]).startswith(
        f"<headsup-alert-{context['delivery_id']}@"
    )
    assert "Manage email updates in My profile" in html
    assert "<script" not in html


def test_template_treats_monitor_and_alert_content_as_text(
    email_manager, context
):
    attack = '<img src=x onerror="alert(1)">'
    context.update(
        {
            "monitor_name": attack,
            "monitor_objective": attack,
            "alert_title": attack,
            "alert_body": attack,
            "alert_paragraphs": [attack],
            "preview": attack,
            "sources": [{"title": attack, "url": "https://example.com"}],
        }
    )
    message = delivery.render_alert_email(context, email_manager)
    html = message.get_body(preferencelist=("html",)).get_content()
    assert attack not in html
    assert "&lt;img" in html
    assert attack in message.get_body(preferencelist=("plain",)).get_content()


def test_subject_cannot_inject_headers(email_manager, context):
    context["monitor_name"] = "F1\r\nBcc: attacker@example.com"
    message = delivery.render_alert_email(context, email_manager)
    assert "\r" not in str(message["Subject"]) and "\n" not in str(
        message["Subject"]
    )
    assert message["Bcc"] is None
    assert message["To"].addresses[0].addr_spec == "reader@example.com"


@pytest.mark.asyncio
async def test_connection_failure_is_safe_to_retry(
    email_manager, context, mocker, monkeypatch
):
    monkeypatch.setattr(email_manager.conf, "SUPPRESS_SEND", 0)
    connection = mocker.patch("app.managers.email.Connection")
    connection.return_value.__aenter__.side_effect = OSError(
        "SECRET SMTP details"
    )
    with pytest.raises(AlertEmailTransportError) as caught:
        await email_manager.send_alert_message(
            delivery.render_alert_email(context, email_manager)
        )
    assert caught.value.retryable and not caught.value.uncertain
    assert "SECRET" not in str(caught.value)


@pytest.mark.parametrize(
    ("error", "retryable", "uncertain"),
    [
        (SMTPDataError(451, "Try later"), True, False),
        (SMTPDataError(550, "Refused"), False, False),
        (
            SMTPRecipientsRefused(
                [SMTPRecipientRefused(450, "Temporary", "reader@example.com")]
            ),
            True,
            False,
        ),
        (TimeoutError("Lost final response"), False, True),
    ],
)
@pytest.mark.asyncio
async def test_smtp_response_classification(
    email_manager, context, mocker, monkeypatch, error, retryable, uncertain
):
    monkeypatch.setattr(email_manager.conf, "SUPPRESS_SEND", 0)
    connection = mocker.patch("app.managers.email.Connection")
    # The implementation uses the actual Connection instance after entering it.
    session = connection.return_value.session
    session.send_message = mocker.AsyncMock(side_effect=error)
    with pytest.raises(AlertEmailTransportError) as caught:
        await email_manager.send_alert_message(
            delivery.render_alert_email(context, email_manager)
        )
    assert caught.value.retryable is retryable
    assert caught.value.uncertain is uncertain


@pytest.mark.asyncio
async def test_quit_failure_does_not_replay_accepted_mail(
    email_manager, context, mocker, monkeypatch
):
    monkeypatch.setattr(email_manager.conf, "SUPPRESS_SEND", 0)
    connection = mocker.patch("app.managers.email.Connection")
    connection.return_value.session.send_message = mocker.AsyncMock()
    connection.return_value.__aexit__.side_effect = OSError("Lost QUIT reply")
    await email_manager.send_alert_message(
        delivery.render_alert_email(context, email_manager)
    )
    connection.return_value.session.send_message.assert_awaited_once()
