"""Render the email gallery with sample data, without connecting to SMTP."""

import argparse
from datetime import UTC, datetime
from pathlib import Path
from tempfile import mkdtemp

from jinja2 import Environment, FileSystemLoader, StrictUndefined


def main() -> None:
    """Write HTML and plain-text previews plus a local gallery."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    output = parser.parse_args().output
    if output is None:
        output = Path(mkdtemp(prefix="headsup-email-preview-"))
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    templates = Path(__file__).resolve().parents[1] / "app/templates/email"
    engine = Environment(
        loader=FileSystemLoader(templates),
        autoescape=True,
        undefined=StrictUndefined,
    )
    context = {
        "name": "Alex",
        "user": "alex@example.com",
        "application": "HeadsUp",
        "base_url": "https://headsup-api.aliahhmad.dev",
        "frontend_url": "https://headsup.aliahhmad.dev",
        "year": datetime.now(UTC).year,
        "verification": "preview-only.verify.not-a-real-token",
        "reset_token": "preview-only.reset.not-a-real-token",
        "heading": "Your account is up to date.",
        "preview_text": "Your latest account update, all in one place.",
        "category": "YOUR ACCOUNT",
        "introduction": "A quick heads-up. Everything is in place.",
        "message": (
            "Your profile details have been updated successfully.\n"
            "You can review your information in your personal space."
        ),
        "action_url": "https://headsup.aliahhmad.dev/profile",
        "action_label": "View my profile",
    }
    names = {
        "welcome": "Email verification",
        "password_reset": "Password reset",
        "password_changed": "Password changed",
        "notification": "General notification",
    }
    for name in names:
        for extension in ("html", "txt"):
            filename = f"{name}.{extension}"
            rendered = engine.get_template(filename).render(**context)
            (output / filename).write_text(rendered, encoding="utf-8")
    links = "".join(
        f'<a href="{name}.html" target="email">{label}</a>'
        f'<a class="plain" href="{name}.txt" target="email">Text</a>'
        for name, label in names.items()
    )
    gallery = """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HeadsUp email previews</title>
<style>
body{margin:0;background:#e2e8f0;color:#0f172a;font:14px Arial,sans-serif}
header{padding:24px;background:#0f172a;color:#f8fafc}
h1{margin:0 0 12px;font-size:22px}p{color:#cbd5e1;line-height:1.6}
nav{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
a,button{padding:10px 12px;background:#4361ee;color:white;border:0;
border-radius:6px;text-decoration:none;font:inherit;cursor:pointer}
a.plain{background:#17233b;color:#cbd5e1;margin-right:14px}
.sizes{padding:16px;display:flex;justify-content:center;gap:8px}
iframe{display:block;border:0;margin:0 auto;width:100%;max-width:760px;
height:1200px;background:#f8fafc;transition:max-width .2s}
button:focus-visible,a:focus-visible{outline:3px solid #5eead4}
</style></head><body><header><h1>HeadsUp email previews</h1>
<p>Sample content only. No emails are sent. Verification and password reset
use these designs now; the other templates are ready for future use.</p>
<nav aria-label="Email templates">LINKS</nav></header>
<div class="sizes"><button type="button" onclick="resizeEmail(760)">
Desktop</button><button type="button" onclick="resizeEmail(390)">
Mobile</button></div>
<iframe name="email" title="Email preview" src="welcome.html"></iframe>
<script>function resizeEmail(width){document.querySelector('iframe')
.style.maxWidth=width+'px'}</script></body></html>"""
    (output / "index.html").write_text(
        gallery.replace("LINKS", links), encoding="utf-8"
    )
    print(f"Email gallery: {output / 'index.html'}")
    print("Rendered four templates in HTML and plain text. No emails sent.")


if __name__ == "__main__":
    main()
