"""Check rendered transactional emails without making SMTP connections."""

from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import BackgroundTasks
from fastapi_mail import FastMail
from jinja2 import StrictUndefined
from pydantic import NameEmail

from app.schemas.email import EmailTemplateSchema


class EmailLinks(HTMLParser):
    """Read actionable links without depending on template whitespace."""

    def __init__(self) -> None:
        """Initialize the link collection."""
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs) -> None:
        """Collect normal email anchors (not Outlook conditional markup)."""
        if tag == "a":
            self.links.extend(value for key, value in attrs if key == "href")


@pytest.mark.unit
class TestEmailTemplates:
    """Verify links, escaping, and HTML/plain-text delivery."""

    @pytest.mark.parametrize(
        ("template", "token_field", "route", "minutes"),
        [
            ("welcome", "verification", "/verify", 10),
            ("password_reset", "reset_token", "/reset-password", 30),
        ],
    )
    @pytest.mark.asyncio
    async def test_transactional_mime(
        self, email_manager, template, token_field, route, minutes
    ) -> None:
        """Both MIME alternatives must preserve tokens and expiry wording."""
        token = "sample.token-with_underscores.signature"  # noqa: S105
        tasks = BackgroundTasks()
        email_manager.template_send(
            tasks,
            EmailTemplateSchema(
                recipients=[NameEmail("Alex", "alex@example.com")],
                subject="Template test",
                template_name=f"{template}.html",
                body={
                    "name": "Alex & Morgan",
                    "user": "alex@example.com",
                    "base_url": "https://api.example.com/",
                    token_field: token,
                },
            ),
        )
        task = tasks.tasks[0]
        message = await FastMail(email_manager.conf).get_message(
            *task.args, **task.kwargs
        )
        assert any(
            part.get_content_subtype() == "alternative"
            for part in message.walk()
        )
        parts = {
            part.get_content_type(): part.get_payload(decode=True).decode()
            for part in message.walk()
            if part.get_content_type() in {"text/html", "text/plain"}
        }
        assert set(parts) == {"text/html", "text/plain"}
        expected_link = f"https://api.example.com{route}?code={token}"
        for content in parts.values():
            assert expected_link in content
            assert f"{minutes} minutes" in content
            assert "HeadsUp" in content
        assert "Hi Alex &amp; Morgan," in parts["text/html"]
        assert "Hi Alex & Morgan," in parts["text/plain"]
        assert "{{" not in parts["text/html"]
        links = EmailLinks()
        links.feed(parts["text/html"])
        action_links = [
            link for link in links.links if urlparse(link).path == route
        ]
        assert len(action_links) == 2  # noqa: PLR2004
        assert all(
            parse_qs(urlparse(link).query)["code"] == [token]
            for link in action_links
        )

    @pytest.mark.parametrize(
        "template",
        ["welcome", "password_reset", "password_changed", "notification"],
    )
    def test_untrusted_text_is_escaped(self, email_manager, template) -> None:
        """Keep names and notification content as text, not injected HTML."""
        engine = email_manager.conf.template_engine()
        engine.undefined = StrictUndefined
        attack = '<img src=x onerror="alert(1)">'
        rendered = engine.get_template(f"{template}.html").render(
            name=attack,
            user="alex@example.com",
            base_url="https://api.example.com",
            frontend_url="https://example.com",
            verification="sample.verify.token",
            reset_token="sample.reset.token",  # noqa: S106
            message=attack,
            heading=attack,
        )
        assert attack not in rendered
        assert "&lt;img" in rendered
        assert attack in unescape(rendered)

    def test_notification_without_action(self, email_manager) -> None:
        """Informational messages can omit a button and recipient name."""
        rendered = (
            email_manager.conf.template_engine()
            .get_template("notification.html")
            .render(message="Your account details have been updated.")
        )
        assert "Hi there," in rendered
        assert "Your account details have been updated." in rendered
        assert 'class="email-button"' not in rendered
        assert "Button not working?" not in rendered
