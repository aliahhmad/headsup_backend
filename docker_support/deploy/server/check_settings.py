"""Validate deploy-only assumptions without printing secrets or opening sockets."""

from urllib.parse import urlsplit

from pydantic import EmailStr, TypeAdapter

from app.config.settings import get_settings


def main() -> None:
    settings = get_settings()
    for name, value in (
        ("BASE_URL", settings.base_url),
        ("FRONTEND_URL", settings.frontend_url),
    ):
        parsed = urlsplit(value or "")
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise SystemExit(f"Set {name} to the exact public HTTPS origin")
    origins = [origin.strip() for origin in settings.cors_origins.split(",")]
    if "*" in origins or settings.frontend_url not in origins:
        raise SystemExit(
            "CORS_ORIGINS must include the exact frontend origin and must not use '*'"
        )
    for secret in (
        settings.secret_key.get_secret_value(),
        settings.db_password.get_secret_value(),
        settings.referral_code.get_secret_value(),
        settings.admin_pages_encryption_key.get_secret_value(),
    ):
        if "REPLACE_" in secret:
            raise SystemExit(
                "Replace generated-secret placeholders before deployment"
            )
    TypeAdapter(EmailStr).validate_python(settings.mail_from)
    if settings.mail_server in {
        "smtp.example.com",
        "mail.server.com",
        "smtp.yourmailprovider.com",
    }:
        raise SystemExit("Set MAIL_SERVER to the real SMTP host")
    if settings.mail_use_credentials and (
        not settings.mail_username
        or not settings.mail_password.get_secret_value()
    ):
        raise SystemExit(
            "Set MAIL_USERNAME and MAIL_PASSWORD for authenticated SMTP"
        )
    if settings.mail_starttls and settings.mail_ssl_tls:
        raise SystemExit("Choose STARTTLS or implicit TLS, not both")
    print("Server settings validated. No SMTP or model request was made.")
    if not settings.openai_api_key.get_secret_value().strip():
        print(
            "OPENAI_API_KEY is empty; live research remains unavailable until configured."
        )


if __name__ == "__main__":
    main()
