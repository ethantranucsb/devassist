import pytest

import devassist


def test_main_prints_greeting(capsys: pytest.CaptureFixture[str]) -> None:
    devassist.main()
    assert "devassist" in capsys.readouterr().out
