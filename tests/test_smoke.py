from importlib.metadata import entry_points

from devassist.cli import app


def test_the_devassist_command_runs_the_cli() -> None:
    (command,) = entry_points(group="console_scripts", name="devassist")

    assert command.load() is app
