#!/usr/bin/env python3
"""Generate private, server-specific defaults without copying local secrets."""

import argparse
import base64
import os
from pathlib import Path
import re
import secrets
from urllib.parse import urlsplit


def https_origin(value: str) -> str:
    if any(
        char.isspace() or ord(char) < 32 or char in "\\\"'`$" for char in value
    ):
        raise argparse.ArgumentTypeError(
            "Use a plain HTTPS origin without whitespace or shell metacharacters"
        )
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise argparse.ArgumentTypeError(
            "Use a public HTTPS origin without a path, credentials or query"
        )
    return value.rstrip("/")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", type=https_origin, required=True)
    parser.add_argument("--frontend-url", type=https_origin, required=True)
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--stack", default="headsup-demo")
    parser.add_argument(
        "--output", type=Path, default=Path(__file__).with_name(".env")
    )
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Choose a loopback port between 1024 and 65535")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", args.stack):
        parser.error(
            "Stack name must start with a lowercase letter and contain lowercase letters, digits or hyphens"
        )
    values = Path(__file__).with_name(".env.example").read_text()
    replacements = {
        "headsup-demo": args.stack,
        "ORIGIN_PORT=8081": f"ORIGIN_PORT={args.port}",
        "https://api.example.com": args.api_url,
        "https://app.example.com": args.frontend_url,
        "DB_PASSWORD=REPLACE_WITH_GENERATED_SECRET": f"DB_PASSWORD={secrets.token_hex(32)}",
        "SECRET_KEY=REPLACE_WITH_GENERATED_SECRET": f"SECRET_KEY={secrets.token_hex(32)}",
        "REPLACE_WITH_GENERATED_FERNET_KEY": base64.urlsafe_b64encode(
            secrets.token_bytes(32)
        ).decode(),
        "REPLACE_WITH_PRIVATE_INVITE_CODE": secrets.token_urlsafe(24),
    }
    for old, new in replacements.items():
        values = values.replace(old, new)
    try:
        descriptor = os.open(
            args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
    except FileExistsError:
        parser.error(
            "Output already exists; edit it rather than overwriting deployment secrets"
        )
    with os.fdopen(descriptor, "w") as file:
        file.write(values)
    print(
        f"Created {args.output} with mode 600. Edit SMTP and OpenAI settings before starting."
    )


if __name__ == "__main__":
    main()
