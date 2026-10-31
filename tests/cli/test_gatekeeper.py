"""Check that startup requires the configuration acknowledgement."""

import subprocess

from app.main import BLIND_USER_ERROR


class TestCLI:
    """Test the 'gatekeeper' functionality."""

    def test_getkeeper(self, monkeypatch) -> None:
        """Test the gatekeeper.

        Set I_READ_THE_DAMN_DOCS to false and run Uvicorn. Startup must
        exit with the configured acknowledgement error code.
        """
        monkeypatch.setenv("I_READ_THE_DAMN_DOCS", "False")

        command = ["uvicorn", "app.main:app"]
        result = subprocess.run(  # noqa: S603
            command,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == BLIND_USER_ERROR
        assert "You didn't read the docs" in result.stderr
