import pytest

from qr_live_scanner_tencent.__main__ import main


def test_gui_cli_dry_run_reports_entrypoint(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["gui", "--dry-run"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "GUI entrypoint ready" in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "payload" not in output.lower()
