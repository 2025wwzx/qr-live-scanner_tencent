from pathlib import Path
from typing import cast

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


def test_gui_snapshot_cli_writes_visual_pngs(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "gui-snapshots"

    exit_code = main(
        [
            "gui-snapshot",
            "--provider",
            "wechat",
            "--output-dir",
            str(output_dir),
        ]
    )
    output = capsys.readouterr().out

    main_window = output_dir / "main-window.png"
    account_dialog = output_dir / "tencent-account-dialog-wechat.png"
    import_dialog = output_dir / "tencent-account-import-dialog-wechat.png"
    smoke_dialog = output_dir / "tencent-account-smoke-dialog-wechat.png"
    assert exit_code == 0
    assert main_window.read_bytes().startswith(b"\x89PNG")
    assert account_dialog.read_bytes().startswith(b"\x89PNG")
    assert import_dialog.read_bytes().startswith(b"\x89PNG")
    assert smoke_dialog.read_bytes().startswith(b"\x89PNG")
    assert str(main_window) in output
    assert str(account_dialog) in output
    assert str(import_dialog) in output
    assert str(smoke_dialog) in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "payload" not in output.lower()

    from PySide6.QtWidgets import QApplication

    app_instance = QApplication.instance()
    assert app_instance is not None
    app = cast(QApplication, app_instance)
    assert app.font().family() in {
        "Noto Sans SC",
        "Microsoft YaHei",
        "Microsoft YaHei UI",
        "SimHei",
        "SimSun",
        "NSimSun",
    }


def test_gui_snapshot_cli_can_render_mock_account_state(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "mock-account-snapshots"

    exit_code = main(
        [
            "gui-snapshot",
            "--provider",
            "wechat",
            "--mock-uid",
            "local-wechat-user",
            "--output-dir",
            str(output_dir),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert (output_dir / "main-window.png").read_bytes().startswith(b"\x89PNG")
    assert (output_dir / "main-window-account-status.png").read_bytes().startswith(b"\x89PNG")
    assert (output_dir / "tencent-account-dialog-wechat.png").read_bytes().startswith(b"\x89PNG")
    assert (output_dir / "tencent-account-import-dialog-wechat.png").read_bytes().startswith(
        b"\x89PNG"
    )
    assert (output_dir / "tencent-account-smoke-dialog-wechat.png").read_bytes().startswith(
        b"\x89PNG"
    )
    assert (output_dir / "tencent-account-dialog-wechat-qr.png").read_bytes().startswith(b"\x89PNG")
    assert str(output_dir / "main-window-account-status.png") in output
    assert "mock account snapshot rendered" in output
    assert "local-wechat-user" not in output
    assert "local-mock-only" not in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "payload" not in output.lower()
