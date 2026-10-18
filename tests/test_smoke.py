import subprocess
import sys
from importlib.metadata import entry_points, version

from devassist.cli import app


def test_the_devassist_command_runs_the_cli() -> None:
    (command,) = entry_points(group="console_scripts", name="devassist")

    assert command.load() is app


def test_python_m_devassist_runs_the_cli() -> None:
    # The fallback for systems whose security policy blocks the devassist launcher.
    result = subprocess.run(
        [sys.executable, "-m", "devassist", "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"devassist {version('devassist')}"
