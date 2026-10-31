"""Deployment setup must isolate secrets and refuse accidental replacement."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = pytest.mark.unit
SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "docker_support/deploy/server/init_env.py"
)


def generate(output: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--api-url",
            "https://api.example.com",
            "--frontend-url",
            "https://app.example.com",
            "--output",
            str(output),
            *extra,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_generated_environment_is_private_and_does_not_echo_secrets(tmp_path):
    output = tmp_path / ".env"
    result = generate(output)
    assert result.returncode == 0, result.stderr
    assert os.stat(output).st_mode & 0o777 == 0o600
    content = output.read_text()
    assert "API_URL=https://api.example.com" in content
    assert "CORS_ORIGINS=https://app.example.com" in content
    secrets = [
        line.split("=", 1)[1]
        for line in content.splitlines()
        if line.startswith(
            (
                "SECRET_KEY=",
                "DB_PASSWORD=",
                "REFERRAL_CODE=",
                "ADMIN_PAGES_ENCRYPTION_KEY=",
            )
        )
    ]
    assert len(set(secrets)) == 4
    assert all(
        "REPLACE_" not in value and value not in result.stdout
        for value in secrets
    )


def test_existing_server_secrets_are_never_overwritten(tmp_path):
    output = tmp_path / ".env"
    output.write_text("existing-private-settings\n")
    result = generate(output)
    assert result.returncode != 0
    assert output.read_text() == "existing-private-settings\n"


@pytest.mark.parametrize(
    "args",
    [
        ("--port", "80"),
        ("--stack", "../other-app"),
        ("--api-url", "http://api.example.com"),
        ("--frontend-url", "https://app.example.com/path"),
        ("--api-url", "https://api.example.com\nSECRET_KEY=bad"),
    ],
)
def test_invalid_inputs_do_not_create_environment_files(tmp_path, args):
    output = tmp_path / ".env"
    result = generate(output, *args)
    assert result.returncode != 0
    assert not output.exists()
