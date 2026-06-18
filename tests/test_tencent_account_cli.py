from pathlib import Path

import httpx
import pytest

import qr_live_scanner_tencent.__main__ as main_module
from qr_live_scanner_tencent.__main__ import main
from qr_live_scanner_tencent.accounts import (
    TencentSession,
    load_tencent_account_qr_login_config,
)
from qr_live_scanner_tencent.accounts.tencent_qr_login import (
    TencentAccountQRLoginError,
    TencentAccountQRLoginProtocolMode,
    TencentAccountQRLoginState,
    TencentAccountQRLoginStatus,
    TencentAccountQRTicket,
)
from qr_live_scanner_tencent.interfaces import (
    AccountStoreError,
    TencentAccountIndexEntry,
    TencentAccountIndexRepairResult,
    TencentLoginProvider,
)


def test_tencent_login_cli_dry_run_writes_demo_qr_without_echoing_secrets(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "tencent-login.png"

    exit_code = main(
        [
            "tencent-login",
            "--provider",
            "qq",
            "--dry-run",
            "--qr-output",
            str(output_path),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert output_path.exists()
    assert output_path.read_bytes().startswith(b"\x89PNG")
    assert "Tencent account QR dry-run image written" in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "ticket" not in output.lower()
    assert "payload" not in output.lower()


def test_tencent_login_cli_dry_run_rejects_invalid_protocol_config(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "tencent-login.png"
    config_path = tmp_path / "tencent-account-login.toml"
    secret = "SECRET_TICKET_VALUE_DO_NOT_LEAK"
    config_path.write_text(
        "\n".join(
            [
                "[account_qr_login.qq]",
                "validated_protocol = true",
                f'fetch_url = "https://example.test/qq/fetch?ticket={secret}"',
                'query_url = "https://example.test/qq/query"',
                'app_id = "verified-app"',
            ]
        ),
        encoding="utf-8",
    )

    exit_code = _run_main(
        [
            "tencent-login",
            "--provider",
            "qq",
            "--dry-run",
            "--protocol-config",
            str(config_path),
            "--qr-output",
            str(output_path),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert not output_path.exists()
    assert "Tencent account QR login failed" in output
    assert "endpoint" in output.lower()
    assert secret not in output
    assert "example.test" not in output
    assert "verified-app" not in output


def test_tencent_login_cli_dry_run_checks_protocol_config_without_http(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "tencent-login.png"
    config_path = tmp_path / "tencent-account-login.toml"
    config_path.write_text(
        "\n".join(
            [
                "[account_qr_login.wechat]",
                "validated_protocol = true",
                'fetch_url = "https://example.test/wechat/fetch"',
                'query_url = "https://example.test/wechat/query"',
                'app_id = "verified-wechat-app"',
            ]
        ),
        encoding="utf-8",
    )

    def fail_if_http_client_is_created(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("dry-run protocol config check must not create HTTP client")

    monkeypatch.setattr(httpx, "AsyncClient", fail_if_http_client_is_created)

    exit_code = _run_main(
        [
            "tencent-login",
            "--provider",
            "wechat",
            "--dry-run",
            "--protocol-config",
            str(config_path),
            "--qr-output",
            str(output_path),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert output_path.exists()
    assert output_path.read_bytes().startswith(b"\x89PNG")
    assert "Tencent protocol config checked" in output
    assert "Tencent account login dry-run ready" in output
    assert "example.test" not in output
    assert "verified-wechat-app" not in output


def test_tencent_login_preflight_accepts_qq_qrconnect_without_http_or_values(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    unused_tcp_port: int,
) -> None:
    config_path = tmp_path / "tencent-account-login.toml"
    config_path.write_text(
        "\n".join(
            [
                "[account_qr_login.qq]",
                "validated_protocol = true",
                'protocol_mode = "qq_qrconnect"',
                'fetch_url = "https://graph.qq.com/oauth2.0/authorize"',
                'query_url = "https://graph.qq.com/oauth2.0/token"',
                'redirect_uri = "https://login.example.test/oauth/qq/callback"',
                f'callback_bind_url = "http://127.0.0.1:{unused_tcp_port}/qq/callback"',
                'app_id = "verified-qq-app"',
            ]
        ),
        encoding="utf-8",
    )

    def fail_if_http_client_is_created(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("preflight must not create HTTP client")

    monkeypatch.setenv("QR_LIVE_SCANNER_TENCENT_QQ_APP_SECRET", "SECRET_QQ_APP_SECRET")
    monkeypatch.setattr(httpx, "AsyncClient", fail_if_http_client_is_created)

    exit_code = _run_main(
        [
            "tencent-login-preflight",
            "--provider",
            "qq",
            "--protocol-config",
            str(config_path),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "Tencent account login preflight passed" in output
    assert "provider=qq" in output
    assert "protocol_mode=qq_qrconnect" in output
    assert "secret_env=present" in output
    assert "callback_bind=available" in output
    assert "real_http=not-called" in output
    assert "SECRET_QQ_APP_SECRET" not in output
    assert "graph.qq.com" not in output
    assert "login.example.test" not in output
    assert "verified-qq-app" not in output
    assert str(unused_tcp_port) not in output


def test_tencent_login_preflight_rejects_missing_wechat_secret_without_values(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    unused_tcp_port: int,
) -> None:
    config_path = tmp_path / "tencent-account-login.toml"
    config_path.write_text(
        "\n".join(
            [
                "[account_qr_login.wechat]",
                "validated_protocol = true",
                'protocol_mode = "wechat_qrconnect"',
                'fetch_url = "https://open.weixin.qq.com/connect/qrconnect"',
                'query_url = "https://api.weixin.qq.com/sns/oauth2/access_token"',
                'redirect_uri = "https://login.example.test/oauth/wechat/callback"',
                f'callback_bind_url = "http://127.0.0.1:{unused_tcp_port}/wechat/callback"',
                'app_id = "verified-wechat-app"',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("QR_LIVE_SCANNER_TENCENT_WECHAT_APP_SECRET", raising=False)

    exit_code = _run_main(
        [
            "tencent-login-preflight",
            "--provider",
            "wechat",
            "--protocol-config",
            str(config_path),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "Tencent account login preflight failed" in output
    assert "provider=wechat" in output
    assert "secret_env" in output
    assert "QR_LIVE_SCANNER_TENCENT_WECHAT_APP_SECRET" in output
    assert "login.example.test" not in output
    assert "verified-wechat-app" not in output
    assert str(unused_tcp_port) not in output


def test_tencent_login_preflight_rejects_busy_callback_bind_port(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    unused_tcp_port: int,
) -> None:
    import socket

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", unused_tcp_port))
    listener.listen(1)
    config_path = tmp_path / "tencent-account-login.toml"
    config_path.write_text(
        "\n".join(
            [
                "[account_qr_login.qq]",
                "validated_protocol = true",
                'protocol_mode = "qq_qrconnect"',
                'fetch_url = "https://graph.qq.com/oauth2.0/authorize"',
                'query_url = "https://graph.qq.com/oauth2.0/token"',
                'redirect_uri = "https://login.example.test/oauth/qq/callback"',
                f'callback_bind_url = "http://127.0.0.1:{unused_tcp_port}/qq/callback"',
                'app_id = "verified-qq-app"',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("QR_LIVE_SCANNER_TENCENT_QQ_APP_SECRET", "SECRET_QQ_APP_SECRET")

    try:
        exit_code = _run_main(
            [
                "tencent-login-preflight",
                "--provider",
                "qq",
                "--protocol-config",
                str(config_path),
            ]
        )
    finally:
        listener.close()
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "Tencent account login preflight failed" in output
    assert "callback_bind" in output
    assert "SECRET_QQ_APP_SECRET" not in output
    assert "login.example.test" not in output
    assert str(unused_tcp_port) not in output


def test_tencent_login_preflight_accepts_callback_file_handoff_without_bind(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "tencent-account-login.toml"
    callback_file = tmp_path / "oauth-callback.txt"
    config_path.write_text(
        "\n".join(
            [
                "[account_qr_login.qq]",
                "validated_protocol = true",
                'protocol_mode = "qq_qrconnect"',
                'fetch_url = "https://graph.qq.com/oauth2.0/authorize"',
                'query_url = "https://graph.qq.com/oauth2.0/token"',
                'redirect_uri = "https://login.example.test/oauth/qq/callback"',
                'app_id = "verified-qq-app"',
            ]
        ),
        encoding="utf-8",
    )

    def fail_if_http_client_is_created(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("preflight must not create HTTP client")

    monkeypatch.setenv("QR_LIVE_SCANNER_TENCENT_QQ_APP_SECRET", "SECRET_QQ_APP_SECRET")
    monkeypatch.setattr(httpx, "AsyncClient", fail_if_http_client_is_created)

    exit_code = _run_main(
        [
            "tencent-login-preflight",
            "--provider",
            "qq",
            "--protocol-config",
            str(config_path),
            "--callback-url-file",
            str(callback_file),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "Tencent account login preflight passed" in output
    assert "provider=qq" in output
    assert "protocol_mode=qq_qrconnect" in output
    assert "secret_env=present" in output
    assert "callback_bind=file-handoff" in output
    assert "real_http=not-called" in output
    assert "SECRET_QQ_APP_SECRET" not in output
    assert "graph.qq.com" not in output
    assert "login.example.test" not in output
    assert "verified-qq-app" not in output
    assert str(callback_file) not in output


def test_tencent_login_config_init_writes_qq_file_handoff_without_echoing_values(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "tencent-account-login.toml"
    app_id = "verified-qq-connect-app"
    redirect_uri = "https://login.example.test/oauth/qq/callback"

    exit_code = _run_main(
        [
            "tencent-login-config-init",
            "--provider",
            "qq",
            "--app-id",
            app_id,
            "--redirect-uri",
            redirect_uri,
            "--output",
            str(output_path),
        ]
    )
    output = capsys.readouterr().out
    config_text = output_path.read_text(encoding="utf-8")
    config = load_tencent_account_qr_login_config(
        output_path,
        TencentLoginProvider.QQ,
        require_callback_bind_url=False,
    )

    assert exit_code == 0
    assert config.protocol_mode is TencentAccountQRLoginProtocolMode.QQ_QRCONNECT
    assert config.app_id == app_id
    assert config.redirect_uri == redirect_uri
    assert "callback_bind_url" not in config_text
    assert 'fetch_url = "https://graph.qq.com/oauth2.0/authorize"' in config_text
    assert 'query_url = "https://graph.qq.com/oauth2.0/token"' in config_text
    assert "Tencent account login config initialized" in output
    assert "provider=qq" in output
    assert "callback_mode=file-handoff" in output
    assert "real_http=not-called" in output
    assert app_id not in output
    assert "login.example.test" not in output
    assert redirect_uri not in output


def test_tencent_login_config_init_writes_wechat_local_bind_without_echoing_values(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    unused_tcp_port: int,
) -> None:
    output_path = tmp_path / "tencent-account-login.toml"
    app_id = "verified-wechat-connect-app"
    redirect_uri = "https://login.example.test/oauth/wechat/callback"
    callback_bind_url = f"http://127.0.0.1:{unused_tcp_port}/wechat/callback"

    exit_code = _run_main(
        [
            "tencent-login-config-init",
            "--provider",
            "wechat",
            "--app-id",
            app_id,
            "--redirect-uri",
            redirect_uri,
            "--callback-mode",
            "local-bind",
            "--callback-bind-url",
            callback_bind_url,
            "--output",
            str(output_path),
        ]
    )
    output = capsys.readouterr().out
    config_text = output_path.read_text(encoding="utf-8")
    config = load_tencent_account_qr_login_config(
        output_path,
        TencentLoginProvider.WECHAT,
    )

    assert exit_code == 0
    assert config.protocol_mode is TencentAccountQRLoginProtocolMode.WECHAT_QRCONNECT
    assert config.app_id == app_id
    assert config.redirect_uri == redirect_uri
    assert config.callback_bind_url == callback_bind_url
    assert 'fetch_url = "https://open.weixin.qq.com/connect/qrconnect"' in config_text
    assert 'query_url = "https://api.weixin.qq.com/sns/oauth2/access_token"' in config_text
    assert f'callback_bind_url = "{callback_bind_url}"' in config_text
    assert "provider=wechat" in output
    assert "callback_mode=local-bind" in output
    assert app_id not in output
    assert "login.example.test" not in output
    assert callback_bind_url not in output
    assert str(unused_tcp_port) not in output


def test_tencent_login_config_init_rejects_overwrite_without_force(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "tencent-account-login.toml"
    output_path.write_text("original\n", encoding="utf-8")

    exit_code = _run_main(
        [
            "tencent-login-config-init",
            "--provider",
            "qq",
            "--app-id",
            "verified-qq-connect-app",
            "--redirect-uri",
            "https://login.example.test/oauth/qq/callback",
            "--output",
            str(output_path),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 1
    assert output_path.read_text(encoding="utf-8") == "original\n"
    assert "output exists" in output
    assert "verified-qq-connect-app" not in output
    assert "login.example.test" not in output


def test_tencent_login_config_init_force_overwrites_existing_file(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "tencent-account-login.toml"
    output_path.write_text("original\n", encoding="utf-8")

    exit_code = _run_main(
        [
            "tencent-login-config-init",
            "--provider",
            "qq",
            "--app-id",
            "verified-qq-connect-app",
            "--redirect-uri",
            "https://login.example.test/oauth/qq/callback",
            "--output",
            str(output_path),
            "--force",
        ]
    )
    output = capsys.readouterr().out
    config_text = output_path.read_text(encoding="utf-8")

    assert exit_code == 0
    assert "original" not in config_text
    assert 'protocol_mode = "qq_qrconnect"' in config_text
    assert "Tencent account login config initialized" in output


def test_tencent_login_config_init_rejects_local_bind_without_callback_url(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "tencent-account-login.toml"

    exit_code = _run_main(
        [
            "tencent-login-config-init",
            "--provider",
            "wechat",
            "--app-id",
            "verified-wechat-connect-app",
            "--redirect-uri",
            "https://login.example.test/oauth/wechat/callback",
            "--callback-mode",
            "local-bind",
            "--output",
            str(output_path),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert not output_path.exists()
    assert "callback bind URL is required" in output
    assert "verified-wechat-connect-app" not in output
    assert "login.example.test" not in output


def test_tencent_login_config_init_rejects_callback_bind_url_in_file_handoff(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    unused_tcp_port: int,
) -> None:
    output_path = tmp_path / "tencent-account-login.toml"
    callback_bind_url = f"http://127.0.0.1:{unused_tcp_port}/qq/callback"

    exit_code = _run_main(
        [
            "tencent-login-config-init",
            "--provider",
            "qq",
            "--app-id",
            "verified-qq-connect-app",
            "--redirect-uri",
            "https://login.example.test/oauth/qq/callback",
            "--callback-bind-url",
            callback_bind_url,
            "--output",
            str(output_path),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert not output_path.exists()
    assert "callback bind URL is only accepted" in output
    assert callback_bind_url not in output
    assert str(unused_tcp_port) not in output


def test_tencent_login_readiness_reports_missing_config_without_http_or_values(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "missing.toml"

    def fail_if_http_client_is_created(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("readiness must not create HTTP client")

    monkeypatch.setattr(httpx, "AsyncClient", fail_if_http_client_is_created)

    exit_code = _run_main(
        [
            "tencent-login-readiness",
            "--provider",
            "qq",
            "--protocol-config",
            str(config_path),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "Tencent account login readiness: not ready" in output
    assert "provider=qq" in output
    assert "config=missing" in output
    assert "secret_env=not-checked" in output
    assert "callback=not-checked" in output
    assert "real_http=not-called" in output
    assert "ready=no" in output
    assert "next=tencent-login-config-init" in output
    assert str(config_path) not in output


def test_tencent_login_readiness_accepts_file_handoff_when_secret_present(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "tencent-account-login.toml"
    app_id = "verified-qq-connect-app"
    redirect_uri = "https://login.example.test/oauth/qq/callback"
    config_path.write_text(
        "\n".join(
            [
                "[account_qr_login.qq]",
                "validated_protocol = true",
                'protocol_mode = "qq_qrconnect"',
                'fetch_url = "https://graph.qq.com/oauth2.0/authorize"',
                'query_url = "https://graph.qq.com/oauth2.0/token"',
                f'redirect_uri = "{redirect_uri}"',
                f'app_id = "{app_id}"',
            ]
        ),
        encoding="utf-8",
    )

    def fail_if_http_client_is_created(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("readiness must not create HTTP client")

    monkeypatch.setenv("QR_LIVE_SCANNER_TENCENT_QQ_APP_SECRET", "SECRET_QQ_APP_SECRET")
    monkeypatch.setattr(httpx, "AsyncClient", fail_if_http_client_is_created)

    exit_code = _run_main(
        [
            "tencent-login-readiness",
            "--provider",
            "qq",
            "--protocol-config",
            str(config_path),
            "--callback-url-file",
            str(tmp_path / "oauth-callback.txt"),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "Tencent account login readiness: ready" in output
    assert "provider=qq" in output
    assert "config=present" in output
    assert "protocol_mode=qq_qrconnect" in output
    assert "secret_env=present" in output
    assert "callback=file-handoff" in output
    assert "ready=yes" in output
    assert "next=tencent-login-preflight" in output
    assert "SECRET_QQ_APP_SECRET" not in output
    assert app_id not in output
    assert "login.example.test" not in output
    assert str(config_path) not in output
    assert "oauth-callback" not in output


def test_tencent_login_readiness_reports_missing_secret_without_values(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "tencent-account-login.toml"
    app_id = "verified-wechat-connect-app"
    redirect_uri = "https://login.example.test/oauth/wechat/callback"
    config_path.write_text(
        "\n".join(
            [
                "[account_qr_login.wechat]",
                "validated_protocol = true",
                'protocol_mode = "wechat_qrconnect"',
                'fetch_url = "https://open.weixin.qq.com/connect/qrconnect"',
                'query_url = "https://api.weixin.qq.com/sns/oauth2/access_token"',
                f'redirect_uri = "{redirect_uri}"',
                f'app_id = "{app_id}"',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("QR_LIVE_SCANNER_TENCENT_WECHAT_APP_SECRET", raising=False)

    exit_code = _run_main(
        [
            "tencent-login-readiness",
            "--provider",
            "wechat",
            "--protocol-config",
            str(config_path),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "Tencent account login readiness: not ready" in output
    assert "provider=wechat" in output
    assert "protocol_mode=wechat_qrconnect" in output
    assert "secret_env=missing:QR_LIVE_SCANNER_TENCENT_WECHAT_APP_SECRET" in output
    assert "callback=file-handoff" in output
    assert "ready=no" in output
    assert "next=fix-readiness" in output
    assert app_id not in output
    assert "login.example.test" not in output


def test_tencent_login_readiness_reports_busy_local_bind_without_port_value(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    unused_tcp_port: int,
) -> None:
    import socket

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", unused_tcp_port))
    listener.listen(1)
    config_path = tmp_path / "tencent-account-login.toml"
    callback_bind_url = f"http://127.0.0.1:{unused_tcp_port}/qq/callback"
    config_path.write_text(
        "\n".join(
            [
                "[account_qr_login.qq]",
                "validated_protocol = true",
                'protocol_mode = "qq_qrconnect"',
                'fetch_url = "https://graph.qq.com/oauth2.0/authorize"',
                'query_url = "https://graph.qq.com/oauth2.0/token"',
                'redirect_uri = "https://login.example.test/oauth/qq/callback"',
                f'callback_bind_url = "{callback_bind_url}"',
                'app_id = "verified-qq-connect-app"',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("QR_LIVE_SCANNER_TENCENT_QQ_APP_SECRET", "SECRET_QQ_APP_SECRET")

    try:
        exit_code = _run_main(
            [
                "tencent-login-readiness",
                "--provider",
                "qq",
                "--protocol-config",
                str(config_path),
            ]
        )
    finally:
        listener.close()
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "callback=local-bind-unavailable" in output
    assert "ready=no" in output
    assert "SECRET_QQ_APP_SECRET" not in output
    assert callback_bind_url not in output
    assert str(unused_tcp_port) not in output
    assert "login.example.test" not in output


def test_tencent_login_cli_mock_confirm_saves_local_session_without_http(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "wechat-login.png"
    saved: list[tuple[TencentSession, bool]] = []
    operations: list[str] = []

    class FakeStore:
        def get_tencent_session(
            self,
            uid: str,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> TencentSession | None:
            assert uid == "local-wechat-user"
            assert provider is TencentLoginProvider.WECHAT
            operations.append("get")
            return None

        def save_tencent_session(self, session: object, *, authorized: bool) -> None:
            assert isinstance(session, TencentSession)
            operations.append("save")
            saved.append((session, authorized))

        def list_tencent_sessions(
            self,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> list[TencentAccountIndexEntry]:
            assert provider is TencentLoginProvider.WECHAT
            operations.append("list")
            return [
                TencentAccountIndexEntry(
                    uid=session.uid,
                    provider=session.provider,
                    authorized=authorized,
                )
                for session, authorized in saved
                if session.provider is provider
            ]

    def fail_if_real_service_is_created(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("mock confirm must not create the real QR login service")

    monkeypatch.setattr(main_module, "KeyringAccountStore", FakeStore)
    monkeypatch.setattr(
        main_module,
        "_new_tencent_account_qr_login_service",
        fail_if_real_service_is_created,
    )

    exit_code = _run_main(
        [
            "tencent-login",
            "--provider",
            "wechat",
            "--mock-confirm",
            "--mock-uid",
            "local-wechat-user",
            "--qr-output",
            str(output_path),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert operations == ["get", "save", "list"]
    assert output_path.exists()
    assert output_path.read_bytes().startswith(b"\x89PNG")
    assert len(saved) == 1
    session, authorized = saved[0]
    assert session.uid == "local-wechat-user"
    assert session.provider is TencentLoginProvider.WECHAT
    assert session.credentials == {"mock_session": "local-mock-only"}
    assert authorized is True
    assert "mock Tencent account session saved" in output
    assert "mock Tencent account index verified" in output
    assert "local-wechat-user" not in output
    assert "local-mock-only" not in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "ticket" not in output.lower()
    assert "payload" not in output.lower()


def test_tencent_login_cli_mock_confirm_fails_when_index_missing_after_save(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "missing-index-login.png"

    class FakeStore:
        def get_tencent_session(
            self,
            uid: str,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> TencentSession | None:
            assert uid == "local-qq-user"
            assert provider is TencentLoginProvider.QQ
            return None

        def save_tencent_session(self, session: object, *, authorized: bool) -> None:
            assert isinstance(session, TencentSession)
            assert session.uid == "local-qq-user"
            assert authorized is True

        def list_tencent_sessions(
            self,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> list[TencentAccountIndexEntry]:
            assert provider is TencentLoginProvider.QQ
            return []

    monkeypatch.setattr(main_module, "KeyringAccountStore", FakeStore)

    exit_code = _run_main(
        [
            "tencent-login",
            "--provider",
            "qq",
            "--mock-confirm",
            "--mock-uid",
            "local-qq-user",
            "--qr-output",
            str(output_path),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 1
    assert not output_path.exists()
    assert "index verification failed" in output
    assert "local-qq-user" not in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "ticket" not in output.lower()
    assert "payload" not in output.lower()


def test_tencent_login_cli_mock_confirm_does_not_overwrite_existing_session(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "existing-qq-login.png"
    operations: list[str] = []

    class FakeStore:
        def get_tencent_session(
            self,
            uid: str,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> TencentSession | None:
            assert uid == "existing-qq-user"
            assert provider is TencentLoginProvider.QQ
            operations.append("get")
            return TencentSession(
                uid=uid,
                provider=provider,
                credentials={"access_token": "SECRET_ACCESS_TOKEN"},
            )

        def save_tencent_session(self, session: object, *, authorized: bool) -> None:
            raise AssertionError("existing Tencent session must not be overwritten")

    monkeypatch.setattr(main_module, "KeyringAccountStore", FakeStore)

    exit_code = _run_main(
        [
            "tencent-login",
            "--provider",
            "qq",
            "--mock-confirm",
            "--mock-uid",
            "existing-qq-user",
            "--qr-output",
            str(output_path),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 1
    assert operations == ["get"]
    assert not output_path.exists()
    assert "already exists" in output
    assert "SECRET_ACCESS_TOKEN" not in output
    assert "existing-qq-user" not in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "ticket" not in output.lower()
    assert "payload" not in output.lower()


def test_tencent_login_cli_mock_confirm_removes_qr_when_save_fails(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "failed-save-login.png"

    class FailingStore:
        def get_tencent_session(
            self,
            uid: str,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> TencentSession | None:
            assert uid == "local-qq-user"
            assert provider is TencentLoginProvider.QQ
            return None

        def save_tencent_session(self, session: object, *, authorized: bool) -> None:
            assert isinstance(session, TencentSession)
            assert authorized is True
            raise AccountStoreError("SECRET_ACCESS_TOKEN should not be visible")

    monkeypatch.setattr(main_module, "KeyringAccountStore", FailingStore)

    exit_code = _run_main(
        [
            "tencent-login",
            "--provider",
            "qq",
            "--mock-confirm",
            "--mock-uid",
            "local-qq-user",
            "--qr-output",
            str(output_path),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert output_path.exists() is False
    assert "credential storage unavailable" in output
    assert "SECRET_ACCESS_TOKEN" not in output
    assert "local-qq-user" not in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "ticket" not in output.lower()
    assert "payload" not in output.lower()


def test_tencent_login_cli_mock_confirm_requires_mock_uid_before_writing_qr(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "mock-login.png"

    def fail_if_real_service_is_created(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("mock confirm must validate before creating services")

    monkeypatch.setattr(
        main_module,
        "_new_tencent_account_qr_login_service",
        fail_if_real_service_is_created,
    )

    exit_code = _run_main(
        [
            "tencent-login",
            "--provider",
            "qq",
            "--mock-confirm",
            "--qr-output",
            str(output_path),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert not output_path.exists()
    assert "mock uid is required" in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()


@pytest.mark.parametrize(
    ("option", "value", "message"),
    [
        ("--timeout-seconds", "0", "timeout seconds must be finite and positive"),
        ("--poll-interval-seconds", "0", "poll interval seconds must be finite and positive"),
    ],
)
def test_tencent_login_cli_validates_timing_before_creating_runtime_resources(
    option: str,
    value: str,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    service_requests: list[TencentLoginProvider] = []

    def fake_service_factory(provider: TencentLoginProvider) -> object:
        service_requests.append(provider)
        raise AssertionError("service should not be created")

    monkeypatch.setattr(main_module, "_new_tencent_account_qr_login_service", fake_service_factory)

    exit_code = main(
        [
            "tencent-login",
            "--provider",
            "qq",
            "--qr-output",
            str(tmp_path / "tencent-login.png"),
            option,
            value,
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert message in output
    assert service_requests == []


def test_tencent_login_cli_uses_local_protocol_config(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "tencent-account-login.toml"
    config_path.write_text(
        "\n".join(
            [
                "[account_qr_login.qq]",
                "validated_protocol = true",
                'fetch_url = "https://example.test/qq/fetch"',
                'query_url = "https://example.test/qq/query"',
                'app_id = "test-app"',
            ]
        ),
        encoding="utf-8",
    )
    captured_configs: list[tuple[bool, str, str, str]] = []

    async def fake_capture(
        service: object,
        *,
        qr_output_path: Path,
        timeout_seconds: float,
        poll_interval_seconds: float,
        open_qr: bool = False,
        open_provider_page: bool = False,
        callback_url_file: Path | None = None,
    ) -> TencentSession:
        assert qr_output_path == tmp_path / "tencent-login.png"
        assert timeout_seconds == 3
        assert poll_interval_seconds == 0.01
        assert open_qr is False
        assert open_provider_page is False
        assert callback_url_file is None
        assert hasattr(service, "config")
        config = service.config
        captured_configs.append(
            (
                bool(config.validated_protocol),
                str(config.fetch_url),
                str(config.query_url),
                str(config.app_id),
            )
        )
        return TencentSession(
            uid="10001",
            provider=TencentLoginProvider.QQ,
            credentials={"access_token": "SECRET_ACCESS_TOKEN", "openid": "SECRET_OPENID"},
        )

    class FakeStore:
        def __init__(self) -> None:
            self.saved: list[tuple[TencentSession, bool]] = []

        def save_tencent_session(self, session: object, *, authorized: bool) -> None:
            assert isinstance(session, TencentSession)
            assert authorized is True
            self.saved.append((session, authorized))

        def list_tencent_sessions(
            self,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> list[TencentAccountIndexEntry]:
            assert provider is TencentLoginProvider.QQ
            return [
                TencentAccountIndexEntry(
                    uid=session.uid,
                    provider=session.provider,
                    authorized=authorized,
                )
                for session, authorized in self.saved
                if session.provider is provider
            ]

    monkeypatch.setattr(main_module, "_capture_tencent_session_from_qr", fake_capture)
    monkeypatch.setattr(main_module, "KeyringAccountStore", FakeStore)

    exit_code = main(
        [
            "tencent-login",
            "--provider",
            "qq",
            "--protocol-config",
            str(config_path),
            "--qr-output",
            str(tmp_path / "tencent-login.png"),
            "--timeout-seconds",
            "3",
            "--poll-interval-seconds",
            "0.01",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert captured_configs == [
        (
            True,
            "https://example.test/qq/fetch",
            "https://example.test/qq/query",
            "test-app",
        )
    ]
    assert "Tencent account session saved" in output
    assert "SECRET_ACCESS_TOKEN" not in output
    assert "10001" not in output


def test_tencent_login_cli_rejects_mismatched_provider_session(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    saved: list[TencentSession] = []

    async def fake_capture(
        service: object,
        *,
        qr_output_path: Path,
        timeout_seconds: float,
        poll_interval_seconds: float,
        open_qr: bool = False,
        open_provider_page: bool = False,
        callback_url_file: Path | None = None,
    ) -> TencentSession:
        assert service is not None
        assert qr_output_path == tmp_path / "tencent-login.png"
        assert timeout_seconds == 3
        assert poll_interval_seconds == 0.01
        assert open_qr is False
        assert open_provider_page is False
        assert callback_url_file is None
        return TencentSession(
            uid="wechat-user",
            provider=TencentLoginProvider.QQ,
            credentials={"access_token": "SECRET_ACCESS_TOKEN", "openid": "SECRET_OPENID"},
        )

    class FakeStore:
        def save_tencent_session(self, session: object, *, authorized: bool) -> None:
            assert authorized is True
            assert isinstance(session, TencentSession)
            saved.append(session)

    monkeypatch.setattr(main_module, "_capture_tencent_session_from_qr", fake_capture)
    monkeypatch.setattr(
        main_module,
        "_new_tencent_account_qr_login_service",
        lambda _provider, **_kwargs: object(),
    )
    monkeypatch.setattr(main_module, "KeyringAccountStore", FakeStore)

    exit_code = main(
        [
            "tencent-login",
            "--provider",
            "wechat",
            "--qr-output",
            str(tmp_path / "tencent-login.png"),
            "--timeout-seconds",
            "3",
            "--poll-interval-seconds",
            "0.01",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert saved == []
    assert "provider mismatch" in output
    assert "wechat-user" not in output
    assert "SECRET_ACCESS_TOKEN" not in output
    assert "SECRET_OPENID" not in output


def test_tencent_login_cli_passes_open_qr_flag(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    captured_open_flags: list[bool] = []

    async def fake_capture(
        service: object,
        *,
        qr_output_path: Path,
        timeout_seconds: float,
        poll_interval_seconds: float,
        open_qr: bool = False,
        open_provider_page: bool = False,
        callback_url_file: Path | None = None,
    ) -> TencentSession:
        assert service is not None
        assert qr_output_path == tmp_path / "tencent-login.png"
        assert timeout_seconds == 3
        assert poll_interval_seconds == 0.01
        captured_open_flags.append(open_qr)
        assert open_provider_page is False
        assert callback_url_file is None
        return TencentSession(
            uid="10001",
            provider=TencentLoginProvider.QQ,
            credentials={"access_token": "SECRET_ACCESS_TOKEN", "openid": "SECRET_OPENID"},
        )

    class FakeStore:
        def __init__(self) -> None:
            self.saved: list[tuple[TencentSession, bool]] = []

        def save_tencent_session(self, session: object, *, authorized: bool) -> None:
            assert isinstance(session, TencentSession)
            self.saved.append((session, authorized))

        def list_tencent_sessions(
            self,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> list[TencentAccountIndexEntry]:
            assert provider is TencentLoginProvider.QQ
            return [
                TencentAccountIndexEntry(
                    uid=session.uid,
                    provider=session.provider,
                    authorized=authorized,
                )
                for session, authorized in self.saved
            ]

    monkeypatch.setattr(main_module, "_capture_tencent_session_from_qr", fake_capture)
    monkeypatch.setattr(
        main_module,
        "_new_tencent_account_qr_login_service",
        lambda _provider, **_kwargs: object(),
    )
    monkeypatch.setattr(main_module, "KeyringAccountStore", FakeStore)

    exit_code = main(
        [
            "tencent-login",
            "--provider",
            "qq",
            "--open-qr",
            "--qr-output",
            str(tmp_path / "tencent-login.png"),
            "--timeout-seconds",
            "3",
            "--poll-interval-seconds",
            "0.01",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert captured_open_flags == [True]
    assert "Tencent account session saved" in output
    assert "SECRET_ACCESS_TOKEN" not in output
    assert "10001" not in output


def test_tencent_login_cli_passes_open_provider_page_flag(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    captured_open_provider_flags: list[bool] = []

    async def fake_capture(
        service: object,
        *,
        qr_output_path: Path,
        timeout_seconds: float,
        poll_interval_seconds: float,
        open_qr: bool = False,
        open_provider_page: bool = False,
        callback_url_file: Path | None = None,
    ) -> TencentSession:
        assert service is not None
        assert qr_output_path == tmp_path / "tencent-login.png"
        assert timeout_seconds == 3
        assert poll_interval_seconds == 0.01
        assert open_qr is False
        captured_open_provider_flags.append(open_provider_page)
        assert callback_url_file is None
        return TencentSession(
            uid="10001",
            provider=TencentLoginProvider.WECHAT,
            credentials={"access_token": "SECRET_ACCESS_TOKEN", "openid": "SECRET_OPENID"},
        )

    class FakeStore:
        def __init__(self) -> None:
            self.saved: list[tuple[TencentSession, bool]] = []

        def save_tencent_session(self, session: object, *, authorized: bool) -> None:
            assert isinstance(session, TencentSession)
            self.saved.append((session, authorized))

        def list_tencent_sessions(
            self,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> list[TencentAccountIndexEntry]:
            assert provider is TencentLoginProvider.WECHAT
            return [
                TencentAccountIndexEntry(
                    uid=session.uid,
                    provider=session.provider,
                    authorized=authorized,
                )
                for session, authorized in self.saved
            ]

    monkeypatch.setattr(main_module, "_capture_tencent_session_from_qr", fake_capture)
    monkeypatch.setattr(
        main_module,
        "_new_tencent_account_qr_login_service",
        lambda _provider, **_kwargs: object(),
    )
    monkeypatch.setattr(main_module, "KeyringAccountStore", FakeStore)

    exit_code = main(
        [
            "tencent-login",
            "--provider",
            "wechat",
            "--open-provider-page",
            "--qr-output",
            str(tmp_path / "tencent-login.png"),
            "--timeout-seconds",
            "3",
            "--poll-interval-seconds",
            "0.01",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert captured_open_provider_flags == [True]
    assert "Tencent account session saved" in output
    assert "SECRET_ACCESS_TOKEN" not in output
    assert "10001" not in output


def test_tencent_login_cli_passes_callback_url_file(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    callback_file = tmp_path / "oauth-callback.txt"
    captured_callback_files: list[Path | None] = []

    async def fake_capture(
        service: object,
        *,
        qr_output_path: Path,
        timeout_seconds: float,
        poll_interval_seconds: float,
        open_qr: bool = False,
        open_provider_page: bool = False,
        callback_url_file: Path | None = None,
    ) -> TencentSession:
        assert service is not None
        assert qr_output_path == tmp_path / "tencent-login.png"
        assert timeout_seconds == 3
        assert poll_interval_seconds == 0.01
        assert open_qr is False
        assert open_provider_page is False
        captured_callback_files.append(callback_url_file)
        return TencentSession(
            uid="10001",
            provider=TencentLoginProvider.QQ,
            credentials={"access_token": "SECRET_ACCESS_TOKEN", "openid": "SECRET_OPENID"},
        )

    class FakeStore:
        def __init__(self) -> None:
            self.saved: list[tuple[TencentSession, bool]] = []

        def save_tencent_session(self, session: object, *, authorized: bool) -> None:
            assert isinstance(session, TencentSession)
            self.saved.append((session, authorized))

        def list_tencent_sessions(
            self,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> list[TencentAccountIndexEntry]:
            assert provider is TencentLoginProvider.QQ
            return [
                TencentAccountIndexEntry(
                    uid=session.uid,
                    provider=session.provider,
                    authorized=authorized,
                )
                for session, authorized in self.saved
            ]

    monkeypatch.setattr(main_module, "_capture_tencent_session_from_qr", fake_capture)
    monkeypatch.setattr(
        main_module,
        "_new_tencent_account_qr_login_service",
        lambda _provider, **_kwargs: object(),
    )
    monkeypatch.setattr(main_module, "KeyringAccountStore", FakeStore)

    exit_code = main(
        [
            "tencent-login",
            "--provider",
            "qq",
            "--callback-url-file",
            str(callback_file),
            "--qr-output",
            str(tmp_path / "tencent-login.png"),
            "--timeout-seconds",
            "3",
            "--poll-interval-seconds",
            "0.01",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert captured_callback_files == [callback_file]
    assert "Tencent account session saved" in output
    assert str(callback_file) not in output
    assert "SECRET_ACCESS_TOKEN" not in output
    assert "10001" not in output


def test_tencent_login_cli_loads_oauth_config_without_bind_for_callback_file(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "tencent-account-login.toml"
    callback_file = tmp_path / "oauth-callback.txt"
    config_path.write_text(
        "\n".join(
            [
                "[account_qr_login.qq]",
                "validated_protocol = true",
                'protocol_mode = "qq_qrconnect"',
                'fetch_url = "https://graph.qq.com/oauth2.0/authorize"',
                'query_url = "https://graph.qq.com/oauth2.0/token"',
                'redirect_uri = "https://login.example.test/oauth/qq/callback"',
                'app_id = "verified-qq-app"',
            ]
        ),
        encoding="utf-8",
    )
    captured: list[tuple[str, str | None]] = []

    async def fake_capture(
        service: object,
        *,
        qr_output_path: Path,
        timeout_seconds: float,
        poll_interval_seconds: float,
        open_qr: bool = False,
        open_provider_page: bool = False,
        callback_url_file: Path | None = None,
    ) -> TencentSession:
        assert qr_output_path == tmp_path / "tencent-login.png"
        assert timeout_seconds == 3
        assert poll_interval_seconds == 0.01
        assert open_qr is False
        assert open_provider_page is False
        assert hasattr(service, "config")
        captured.append((service.config.callback_bind_url, str(callback_url_file)))
        return TencentSession(
            uid="10001",
            provider=TencentLoginProvider.QQ,
            credentials={"access_token": "SECRET_ACCESS_TOKEN", "openid": "SECRET_OPENID"},
        )

    class FakeStore:
        def __init__(self) -> None:
            self.saved: list[tuple[TencentSession, bool]] = []

        def save_tencent_session(self, session: object, *, authorized: bool) -> None:
            assert isinstance(session, TencentSession)
            self.saved.append((session, authorized))

        def list_tencent_sessions(
            self,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> list[TencentAccountIndexEntry]:
            assert provider is TencentLoginProvider.QQ
            return [
                TencentAccountIndexEntry(
                    uid=session.uid,
                    provider=session.provider,
                    authorized=authorized,
                )
                for session, authorized in self.saved
            ]

    monkeypatch.setattr(main_module, "_capture_tencent_session_from_qr", fake_capture)
    monkeypatch.setattr(main_module, "KeyringAccountStore", FakeStore)

    exit_code = main(
        [
            "tencent-login",
            "--provider",
            "qq",
            "--protocol-config",
            str(config_path),
            "--callback-url-file",
            str(callback_file),
            "--qr-output",
            str(tmp_path / "tencent-login.png"),
            "--timeout-seconds",
            "3",
            "--poll-interval-seconds",
            "0.01",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert captured == [("", str(callback_file))]
    assert "Tencent account session saved" in output
    assert "login.example.test" not in output
    assert "verified-qq-app" not in output
    assert str(callback_file) not in output
    assert "SECRET_ACCESS_TOKEN" not in output
    assert "10001" not in output


def test_tencent_login_cli_scans_qr_and_saves_without_echoing_secrets(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    saved: list[tuple[TencentSession, bool]] = []
    rendered: list[tuple[str, Path]] = []
    closed: list[bool] = []

    class FakeService:
        def __init__(self, **_kwargs: object) -> None:
            self.statuses = [
                TencentAccountQRLoginStatus(
                    provider=TencentLoginProvider.QQ,
                    state=TencentAccountQRLoginState.WAITING,
                ),
                TencentAccountQRLoginStatus(
                    provider=TencentLoginProvider.QQ,
                    state=TencentAccountQRLoginState.SCANNED,
                ),
                TencentAccountQRLoginStatus(
                    provider=TencentLoginProvider.QQ,
                    state=TencentAccountQRLoginState.CONFIRMED,
                    session=TencentSession(
                        uid="10001",
                        provider=TencentLoginProvider.QQ,
                        credentials={
                            "access_token": "SECRET_ACCESS_TOKEN",
                            "openid": "SECRET_OPENID",
                        },
                    ),
                ),
            ]

        async def fetch_qr(self) -> TencentAccountQRTicket:
            return TencentAccountQRTicket(
                provider=TencentLoginProvider.QQ,
                app_id="test-app",
                ticket="SECRET_TICKET",
                qr_url="https://example.test/qq/qr?ticket=SECRET_TICKET",
                device_id="0123456789abcdef0123456789abcdef",
            )

        async def query_qr(self, ticket: object) -> TencentAccountQRLoginStatus:
            assert isinstance(ticket, TencentAccountQRTicket)
            return self.statuses.pop(0)

        def save_confirmed_session(
            self,
            status: TencentAccountQRLoginStatus,
            account_store: object,
        ) -> TencentSession:
            assert status.session is not None
            assert isinstance(account_store, FakeStore)
            account_store.save_tencent_session(status.session, authorized=True)
            return status.session

        def write_qr_png(self, payload_ticket: TencentAccountQRTicket, output_path: Path) -> None:
            rendered.append((payload_ticket.qr_url, output_path))
            output_path.write_bytes(b"PNG")

        async def aclose(self) -> None:
            closed.append(True)

    class FakeStore:
        def save_tencent_session(self, session: object, *, authorized: bool) -> None:
            assert isinstance(session, TencentSession)
            saved.append((session, authorized))

        def list_tencent_sessions(
            self,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> list[TencentAccountIndexEntry]:
            assert provider is TencentLoginProvider.QQ
            return [
                TencentAccountIndexEntry(
                    uid=session.uid,
                    provider=session.provider,
                    authorized=authorized,
                )
                for session, authorized in saved
                if session.provider is provider
            ]

    monkeypatch.setattr(
        main_module,
        "_new_tencent_account_qr_login_service",
        lambda _provider, **_kwargs: FakeService(),
    )
    monkeypatch.setattr(main_module, "KeyringAccountStore", FakeStore)

    exit_code = main(
        [
            "tencent-login",
            "--provider",
            "qq",
            "--qr-output",
            str(tmp_path / "tencent-login.png"),
            "--timeout-seconds",
            "3",
            "--poll-interval-seconds",
            "0.01",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert saved and saved[0][1] is True
    assert rendered == [
        (
            "https://example.test/qq/qr?ticket=SECRET_TICKET",
            tmp_path / "tencent-login.png",
        )
    ]
    assert closed == [True]
    assert not (tmp_path / "tencent-login.png").exists()
    assert "Tencent account QR image written" in output
    assert "Tencent account session saved" in output
    assert "Tencent account index verified" in output
    assert "10001" not in output
    assert "SECRET_TICKET" not in output
    assert "SECRET_ACCESS_TOKEN" not in output
    assert "SECRET_OPENID" not in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()


def test_tencent_login_cli_redacts_storage_errors_after_confirmed_session(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    closed: list[bool] = []

    class FakeService:
        async def fetch_qr(self) -> TencentAccountQRTicket:
            return TencentAccountQRTicket(
                provider=TencentLoginProvider.QQ,
                app_id="test-app",
                ticket="SECRET_TICKET",
                qr_url="https://example.test/qq/qr?ticket=SECRET_TICKET",
                device_id="0123456789abcdef0123456789abcdef",
            )

        async def query_qr(self, ticket: object) -> TencentAccountQRLoginStatus:
            assert isinstance(ticket, TencentAccountQRTicket)
            return TencentAccountQRLoginStatus(
                provider=TencentLoginProvider.QQ,
                state=TencentAccountQRLoginState.CONFIRMED,
                session=TencentSession(
                    uid="10001",
                    provider=TencentLoginProvider.QQ,
                    credentials={
                        "access_token": "SECRET_ACCESS_TOKEN",
                        "openid": "SECRET_OPENID",
                    },
                ),
            )

        def write_qr_png(self, payload_ticket: TencentAccountQRTicket, output_path: Path) -> None:
            assert payload_ticket.ticket == "SECRET_TICKET"
            output_path.write_bytes(b"PNG")

        async def aclose(self) -> None:
            closed.append(True)

    class FailingStore:
        def save_tencent_session(self, session: object, *, authorized: bool) -> None:
            assert isinstance(session, TencentSession)
            assert authorized is True
            raise AccountStoreError("SECRET_ACCESS_TOKEN should not be visible")

    monkeypatch.setattr(
        main_module,
        "_new_tencent_account_qr_login_service",
        lambda _provider, **_kwargs: FakeService(),
    )
    monkeypatch.setattr(main_module, "KeyringAccountStore", FailingStore)

    exit_code = main(
        [
            "tencent-login",
            "--provider",
            "qq",
            "--qr-output",
            str(tmp_path / "tencent-login.png"),
            "--timeout-seconds",
            "3",
            "--poll-interval-seconds",
            "0.01",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert closed == [True]
    assert "credential storage unavailable" in output
    assert "SECRET_TICKET" not in output
    assert "SECRET_ACCESS_TOKEN" not in output
    assert "SECRET_OPENID" not in output
    assert "10001" not in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()


def test_tencent_status_cli_reports_saved_authorized_without_echoing_secrets(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    operations: list[str] = []

    class FakeStore:
        def get_tencent_session(
            self,
            uid: str,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> TencentSession | None:
            assert uid == "10001"
            assert provider is TencentLoginProvider.QQ
            operations.append("get")
            return TencentSession(
                uid=uid,
                provider=provider,
                credentials={"access_token": "SECRET_ACCESS_TOKEN", "openid": "SECRET_OPENID"},
            )

        def is_tencent_authorized(
            self,
            uid: str,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> bool:
            assert uid == "10001"
            assert provider is TencentLoginProvider.QQ
            operations.append("authorized")
            return True

        def list_tencent_sessions(
            self,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> list[TencentAccountIndexEntry]:
            assert provider is TencentLoginProvider.QQ
            operations.append("list")
            return [
                TencentAccountIndexEntry(
                    uid="10001",
                    provider=provider,
                    authorized=True,
                )
            ]

    monkeypatch.setattr(main_module, "KeyringAccountStore", FakeStore)

    exit_code = main(["tencent-status", "--provider", "qq", "--uid", "10001"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert operations == ["get", "authorized", "list"]
    assert "saved and authorized" in output
    assert "Tencent account index verified" in output
    assert "10001" not in output
    assert "SECRET_ACCESS_TOKEN" not in output
    assert "SECRET_OPENID" not in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()


def test_tencent_status_cli_fails_when_index_missing_after_read(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeStore:
        def get_tencent_session(
            self,
            uid: str,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> TencentSession | None:
            assert uid == "10001"
            assert provider is TencentLoginProvider.QQ
            return TencentSession(
                uid=uid,
                provider=provider,
                credentials={"access_token": "SECRET_ACCESS_TOKEN"},
            )

        def is_tencent_authorized(
            self,
            uid: str,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> bool:
            assert uid == "10001"
            assert provider is TencentLoginProvider.QQ
            return True

        def list_tencent_sessions(
            self,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> list[TencentAccountIndexEntry]:
            assert provider is TencentLoginProvider.QQ
            return []

    monkeypatch.setattr(main_module, "KeyringAccountStore", FakeStore)

    exit_code = main(["tencent-status", "--provider", "qq", "--uid", "10001"])
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "index verification failed" in output
    assert "10001" not in output
    assert "SECRET_ACCESS_TOKEN" not in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "ticket" not in output.lower()
    assert "payload" not in output.lower()


def test_tencent_list_cli_reports_index_without_echoing_identifiers(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeStore:
        def list_tencent_sessions(
            self,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> list[TencentAccountIndexEntry]:
            assert provider is TencentLoginProvider.QQ
            return [
                TencentAccountIndexEntry(
                    uid="10001",
                    provider=TencentLoginProvider.QQ,
                    authorized=True,
                ),
                TencentAccountIndexEntry(
                    uid="10002",
                    provider=TencentLoginProvider.QQ,
                    authorized=False,
                ),
            ]

    monkeypatch.setattr(main_module, "KeyringAccountStore", FakeStore)

    exit_code = _run_main(["tencent-list", "--provider", "qq"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "Tencent account sessions: 2" in output
    assert "#1 provider=qq authorized=yes" in output
    assert "#2 provider=qq authorized=no" in output
    assert "10001" not in output
    assert "10002" not in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "ticket" not in output.lower()
    assert "payload" not in output.lower()


def test_tencent_list_cli_redacts_storage_errors(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeStore:
        def list_tencent_sessions(
            self,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> list[TencentAccountIndexEntry]:
            raise AccountStoreError("SECRET_ACCESS_TOKEN should not be visible")

    monkeypatch.setattr(main_module, "KeyringAccountStore", FakeStore)

    exit_code = _run_main(["tencent-list", "--provider", "qq"])
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "credential storage unavailable" in output
    assert "SECRET_ACCESS_TOKEN" not in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()


def test_tencent_repair_index_cli_reports_counts_without_echoing_identifiers(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeStore:
        def repair_tencent_index(
            self,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> TencentAccountIndexRepairResult:
            assert provider is TencentLoginProvider.WECHAT
            return TencentAccountIndexRepairResult(
                provider=provider,
                entries=[
                    TencentAccountIndexEntry(
                        uid="local-wechat-user",
                        provider=provider,
                        authorized=True,
                    )
                ],
                rebuilt_index=True,
                removed_stale_entries=2,
            )

    monkeypatch.setattr(main_module, "KeyringAccountStore", FakeStore)

    exit_code = main(["tencent-repair-index", "--provider", "wechat"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "Tencent account index checked" in output
    assert "provider=wechat" in output
    assert "sessions=1" in output
    assert "rebuilt=yes" in output
    assert "stale_removed=2" in output
    assert "local-wechat-user" not in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "ticket" not in output.lower()
    assert "payload" not in output.lower()


def test_tencent_repair_index_cli_redacts_storage_errors(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeStore:
        def repair_tencent_index(
            self,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> TencentAccountIndexRepairResult:
            raise AccountStoreError("SECRET_ACCESS_TOKEN should not be visible")

    monkeypatch.setattr(main_module, "KeyringAccountStore", FakeStore)

    exit_code = main(["tencent-repair-index", "--provider", "qq"])
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "credential storage unavailable" in output
    assert "SECRET_ACCESS_TOKEN" not in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()


def test_tencent_status_cli_redacts_storage_errors(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeStore:
        def get_tencent_session(
            self,
            uid: str,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> TencentSession | None:
            raise AccountStoreError("SECRET_ACCESS_TOKEN should not be visible")

        def is_tencent_authorized(
            self,
            uid: str,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> bool:
            raise AssertionError("authorization should not run after storage failure")

    monkeypatch.setattr(main_module, "KeyringAccountStore", FakeStore)

    exit_code = main(["tencent-status", "--provider", "qq", "--uid", "10001"])
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "credential storage unavailable" in output
    assert "SECRET_ACCESS_TOKEN" not in output
    assert "10001" not in output


def test_tencent_delete_cli_removes_saved_session_without_echoing_identifiers(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    operations: list[str] = []

    class FakeStore:
        def delete_tencent_session(
            self,
            uid: str,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> None:
            assert uid == "local-wechat-user"
            assert provider is TencentLoginProvider.WECHAT
            operations.append("delete")

        def repair_tencent_index(
            self,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> TencentAccountIndexRepairResult:
            assert provider is TencentLoginProvider.WECHAT
            operations.append("repair")
            return TencentAccountIndexRepairResult(provider=provider, entries=[])

    monkeypatch.setattr(main_module, "KeyringAccountStore", FakeStore)

    exit_code = main(["tencent-delete", "--provider", "wechat", "--uid", "local-wechat-user"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert operations == ["delete", "repair"]
    assert "Tencent account session deleted" in output
    assert "Tencent account index cleanup verified" in output
    assert "local-wechat-user" not in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "ticket" not in output.lower()
    assert "payload" not in output.lower()


def test_tencent_delete_cli_fails_when_index_still_contains_deleted_uid(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeStore:
        def delete_tencent_session(
            self,
            uid: str,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> None:
            assert uid == "local-wechat-user"
            assert provider is TencentLoginProvider.WECHAT

        def repair_tencent_index(
            self,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> TencentAccountIndexRepairResult:
            assert provider is TencentLoginProvider.WECHAT
            return TencentAccountIndexRepairResult(
                provider=provider,
                entries=[
                    TencentAccountIndexEntry(
                        uid="local-wechat-user",
                        provider=provider,
                        authorized=False,
                    )
                ],
            )

    monkeypatch.setattr(main_module, "KeyringAccountStore", FakeStore)

    exit_code = main(["tencent-delete", "--provider", "wechat", "--uid", "local-wechat-user"])
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "index cleanup missing" in output
    assert "local-wechat-user" not in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "ticket" not in output.lower()
    assert "payload" not in output.lower()


def test_tencent_delete_cli_redacts_storage_errors(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeStore:
        def delete_tencent_session(
            self,
            uid: str,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> None:
            assert uid == "10001"
            assert provider is TencentLoginProvider.QQ
            raise AccountStoreError("SECRET_ACCESS_TOKEN should not be visible")

    monkeypatch.setattr(main_module, "KeyringAccountStore", FakeStore)

    exit_code = main(["tencent-delete", "--provider", "qq", "--uid", "10001"])
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "credential storage unavailable" in output
    assert "SECRET_ACCESS_TOKEN" not in output
    assert "10001" not in output


def test_tencent_account_smoke_cli_saves_verifies_and_cleans_up_without_http(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    operations: list[str] = []

    class FakeStore:
        def __init__(self) -> None:
            self.sessions: dict[tuple[TencentLoginProvider, str], TencentSession] = {}
            self.authorized: set[tuple[TencentLoginProvider, str]] = set()

        def save_tencent_session(self, session: object, *, authorized: bool) -> None:
            assert isinstance(session, TencentSession)
            assert session.provider is TencentLoginProvider.WECHAT
            assert session.uid == "local-wechat-user"
            assert session.credentials == {"mock_session": "local-smoke-only"}
            operations.append("save")
            key = (session.provider, session.uid)
            self.sessions[key] = session
            if authorized:
                self.authorized.add(key)

        def get_tencent_session(
            self,
            uid: str,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> TencentSession | None:
            assert uid == "local-wechat-user"
            assert provider is TencentLoginProvider.WECHAT
            operations.append("get")
            return self.sessions.get((provider, uid))

        def is_tencent_authorized(
            self,
            uid: str,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> bool:
            assert uid == "local-wechat-user"
            assert provider is TencentLoginProvider.WECHAT
            operations.append("authorized")
            return (provider, uid) in self.authorized

        def list_tencent_sessions(
            self,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> list[TencentAccountIndexEntry]:
            assert provider is TencentLoginProvider.WECHAT
            operations.append("list")
            return [
                TencentAccountIndexEntry(
                    uid=uid,
                    provider=provider,
                    authorized=(provider, uid) in self.authorized,
                )
                for session_provider, uid in self.sessions
                if session_provider is provider
            ]

        def delete_tencent_session(
            self,
            uid: str,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> None:
            assert uid == "local-wechat-user"
            assert provider is TencentLoginProvider.WECHAT
            operations.append("delete")
            self.sessions.pop((provider, uid), None)
            self.authorized.discard((provider, uid))

    fake_store = FakeStore()

    def fail_if_real_service_is_created(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("local account smoke must not create a real QR login service")

    monkeypatch.setattr(main_module, "KeyringAccountStore", lambda: fake_store)
    monkeypatch.setattr(
        main_module,
        "_new_tencent_account_qr_login_service",
        fail_if_real_service_is_created,
    )

    exit_code = main(
        [
            "tencent-account-smoke",
            "--provider",
            "wechat",
            "--uid",
            "local-wechat-user",
            "--cleanup",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert operations == ["get", "save", "get", "authorized", "list", "delete", "list"]
    assert fake_store.sessions == {}
    assert fake_store.authorized == set()
    assert "Tencent account local smoke passed" in output
    assert "Tencent account local index verified" in output
    assert "Tencent account local smoke cleaned up" in output
    assert "Tencent account local index cleaned up" in output
    assert "local-wechat-user" not in output
    assert "local-smoke-only" not in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "ticket" not in output.lower()
    assert "payload" not in output.lower()


def test_tencent_account_smoke_cli_fails_when_index_missing_after_save(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeStore:
        def __init__(self) -> None:
            self.session = TencentSession(
                uid="10001",
                provider=TencentLoginProvider.QQ,
                credentials={"mock_session": "local-smoke-only"},
            )

        def get_tencent_session(
            self,
            uid: str,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> TencentSession | None:
            assert uid == "10001"
            assert provider is TencentLoginProvider.QQ
            return None if not hasattr(self, "saved") else self.session

        def save_tencent_session(self, session: object, *, authorized: bool) -> None:
            assert isinstance(session, TencentSession)
            assert authorized is True
            self.saved = True

        def is_tencent_authorized(
            self,
            uid: str,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> bool:
            assert uid == "10001"
            assert provider is TencentLoginProvider.QQ
            return True

        def list_tencent_sessions(
            self,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> list[TencentAccountIndexEntry]:
            assert provider is TencentLoginProvider.QQ
            return []

    monkeypatch.setattr(main_module, "KeyringAccountStore", FakeStore)

    exit_code = main(["tencent-account-smoke", "--provider", "qq", "--uid", "10001"])
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "local smoke failed: index missing" in output
    assert "10001" not in output
    assert "local-smoke-only" not in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "ticket" not in output.lower()
    assert "payload" not in output.lower()


def test_tencent_account_smoke_cli_redacts_storage_errors(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeStore:
        def get_tencent_session(
            self,
            uid: str,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> TencentSession | None:
            assert uid == "10001"
            assert provider is TencentLoginProvider.QQ
            raise AccountStoreError("SECRET_ACCESS_TOKEN should not be visible")

        def save_tencent_session(self, session: object, *, authorized: bool) -> None:
            raise AssertionError("save should not run after preflight storage failure")

    monkeypatch.setattr(main_module, "KeyringAccountStore", FakeStore)

    exit_code = main(["tencent-account-smoke", "--provider", "qq", "--uid", "10001"])
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "credential storage unavailable" in output
    assert "SECRET_ACCESS_TOKEN" not in output
    assert "10001" not in output


def test_tencent_account_smoke_cli_does_not_overwrite_existing_session(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    operations: list[str] = []

    class FakeStore:
        def get_tencent_session(
            self,
            uid: str,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> TencentSession | None:
            assert uid == "10001"
            assert provider is TencentLoginProvider.QQ
            operations.append("get")
            return TencentSession(
                uid=uid,
                provider=provider,
                credentials={"access_token": "SECRET_ACCESS_TOKEN"},
            )

        def save_tencent_session(self, session: object, *, authorized: bool) -> None:
            raise AssertionError("existing Tencent session must not be overwritten")

    monkeypatch.setattr(main_module, "KeyringAccountStore", FakeStore)

    exit_code = main(["tencent-account-smoke", "--provider", "qq", "--uid", "10001"])
    output = capsys.readouterr().out

    assert exit_code == 1
    assert operations == ["get"]
    assert "already exists" in output
    assert "SECRET_ACCESS_TOKEN" not in output
    assert "10001" not in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()


async def test_tencent_login_cli_closes_service_when_qr_fetch_fails(tmp_path: Path) -> None:
    closed: list[bool] = []

    class FailingService:
        async def fetch_qr(self) -> TencentAccountQRTicket:
            raise TencentAccountQRLoginError("Tencent account QR fetch HTTP failed")

        async def query_qr(self, ticket: TencentAccountQRTicket) -> TencentAccountQRLoginStatus:
            raise AssertionError("query should not run after fetch failure")

        def write_qr_png(self, ticket: TencentAccountQRTicket, output_path: Path) -> None:
            raise AssertionError("QR should not render after fetch failure")

        async def aclose(self) -> None:
            closed.append(True)

    with pytest.raises(TencentAccountQRLoginError, match="fetch HTTP failed"):
        await main_module._capture_tencent_session_from_qr(
            FailingService(),
            qr_output_path=tmp_path / "tencent-login.png",
            timeout_seconds=1.0,
            poll_interval_seconds=0.01,
        )

    assert closed == [True]


async def test_tencent_login_capture_opens_qr_without_echoing_payload(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    opened: list[Path] = []
    closed: list[bool] = []

    class FakeService:
        async def fetch_qr(self) -> TencentAccountQRTicket:
            return TencentAccountQRTicket(
                provider=TencentLoginProvider.QQ,
                app_id="test-app",
                ticket="SECRET_TICKET",
                qr_url="https://example.test/qq/qr?ticket=SECRET_TICKET",
                device_id="0123456789abcdef0123456789abcdef",
            )

        async def query_qr(self, ticket: TencentAccountQRTicket) -> TencentAccountQRLoginStatus:
            assert ticket.ticket == "SECRET_TICKET"
            return TencentAccountQRLoginStatus(
                provider=TencentLoginProvider.QQ,
                state=TencentAccountQRLoginState.CONFIRMED,
                session=TencentSession(
                    uid="10001",
                    provider=TencentLoginProvider.QQ,
                    credentials={
                        "access_token": "SECRET_ACCESS_TOKEN",
                        "openid": "SECRET_OPENID",
                    },
                ),
            )

        def write_qr_png(self, ticket: TencentAccountQRTicket, output_path: Path) -> None:
            assert ticket.ticket == "SECRET_TICKET"
            output_path.write_bytes(b"PNG")

        async def aclose(self) -> None:
            closed.append(True)

    def fake_open(path: Path) -> None:
        opened.append(path)

    qr_path = tmp_path / "tencent-login.png"
    monkeypatch.setattr(main_module, "_open_tencent_qr_png", fake_open)

    session = await main_module._capture_tencent_session_from_qr(
        FakeService(),
        qr_output_path=qr_path,
        timeout_seconds=1.0,
        poll_interval_seconds=0.01,
        open_qr=True,
    )
    output = capsys.readouterr().out

    assert session.provider is TencentLoginProvider.QQ
    assert opened == [qr_path]
    assert closed == [True]
    assert not qr_path.exists()
    assert "Tencent account QR image opened" in output
    assert "SECRET_TICKET" not in output
    assert "SECRET_ACCESS_TOKEN" not in output
    assert "SECRET_OPENID" not in output


async def test_tencent_login_capture_opens_provider_page_without_echoing_payload(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    opened: list[str] = []
    closed: list[bool] = []

    class FakeService:
        async def fetch_qr(self) -> TencentAccountQRTicket:
            return TencentAccountQRTicket(
                provider=TencentLoginProvider.WECHAT,
                app_id="test-app",
                ticket="SECRET_TICKET",
                qr_url="https://open.weixin.qq.com/connect/qrconnect?state=SECRET_TICKET",
                device_id="0123456789abcdef0123456789abcdef",
            )

        async def query_qr(self, ticket: TencentAccountQRTicket) -> TencentAccountQRLoginStatus:
            assert ticket.ticket == "SECRET_TICKET"
            return TencentAccountQRLoginStatus(
                provider=TencentLoginProvider.WECHAT,
                state=TencentAccountQRLoginState.CONFIRMED,
                session=TencentSession(
                    uid="10001",
                    provider=TencentLoginProvider.WECHAT,
                    credentials={
                        "access_token": "SECRET_ACCESS_TOKEN",
                        "openid": "SECRET_OPENID",
                    },
                ),
            )

        def write_qr_png(self, ticket: TencentAccountQRTicket, output_path: Path) -> None:
            assert ticket.ticket == "SECRET_TICKET"
            output_path.write_bytes(b"PNG")

        async def aclose(self) -> None:
            closed.append(True)

    def fake_open(target: str | Path, _message: str) -> None:
        opened.append(str(target))

    qr_path = tmp_path / "tencent-login.png"
    monkeypatch.setattr(main_module, "_open_tencent_local_target", fake_open)

    session = await main_module._capture_tencent_session_from_qr(
        FakeService(),
        qr_output_path=qr_path,
        timeout_seconds=1.0,
        poll_interval_seconds=0.01,
        open_provider_page=True,
    )
    output = capsys.readouterr().out

    assert session.provider is TencentLoginProvider.WECHAT
    assert opened == ["https://open.weixin.qq.com/connect/qrconnect?state=SECRET_TICKET"]
    assert closed == [True]
    assert not qr_path.exists()
    assert "Tencent account provider authorization page opened" in output
    assert "open.weixin.qq.com" not in output
    assert "SECRET_TICKET" not in output
    assert "SECRET_ACCESS_TOKEN" not in output
    assert "SECRET_OPENID" not in output


async def test_tencent_login_capture_accepts_callback_url_file_without_echoing_values(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    accepted_callbacks: list[tuple[str, str]] = []
    closed: list[bool] = []

    class FakeService:
        async def fetch_qr(self) -> TencentAccountQRTicket:
            return TencentAccountQRTicket(
                provider=TencentLoginProvider.QQ,
                app_id="test-app",
                ticket="SECRET_STATE",
                qr_url="https://graph.qq.com/oauth2.0/authorize?state=SECRET_STATE",
                device_id="0123456789abcdef0123456789abcdef",
            )

        def write_qr_png(self, ticket: TencentAccountQRTicket, output_path: Path) -> None:
            assert ticket.ticket == "SECRET_STATE"
            output_path.write_bytes(b"PNG")

        def accept_oauth_callback(self, *, state: str, code: str) -> None:
            accepted_callbacks.append((state, code))

        async def query_qr(self, ticket: TencentAccountQRTicket) -> TencentAccountQRLoginStatus:
            assert ticket.ticket == "SECRET_STATE"
            if not accepted_callbacks:
                return TencentAccountQRLoginStatus(
                    provider=TencentLoginProvider.QQ,
                    state=TencentAccountQRLoginState.WAITING,
                )
            return TencentAccountQRLoginStatus(
                provider=TencentLoginProvider.QQ,
                state=TencentAccountQRLoginState.CONFIRMED,
                session=TencentSession(
                    uid="10001",
                    provider=TencentLoginProvider.QQ,
                    credentials={
                        "access_token": "SECRET_ACCESS_TOKEN",
                        "openid": "SECRET_OPENID",
                    },
                ),
            )

        async def aclose(self) -> None:
            closed.append(True)

    callback_file = tmp_path / "oauth-callback.txt"
    callback_file.write_text(
        "https://login.example.test/qq/callback?code=SECRET_CODE&state=SECRET_STATE",
        encoding="utf-8",
    )
    qr_path = tmp_path / "tencent-login.png"

    session = await main_module._capture_tencent_session_from_qr(
        FakeService(),
        qr_output_path=qr_path,
        timeout_seconds=1.0,
        poll_interval_seconds=0.01,
        callback_url_file=callback_file,
    )
    output = capsys.readouterr().out

    assert session.provider is TencentLoginProvider.QQ
    assert accepted_callbacks == [("SECRET_STATE", "SECRET_CODE")]
    assert closed == [True]
    assert not callback_file.exists()
    assert not qr_path.exists()
    assert "Tencent account OAuth callback file accepted" in output
    assert "SECRET_STATE" not in output
    assert "SECRET_CODE" not in output
    assert "SECRET_ACCESS_TOKEN" not in output
    assert "SECRET_OPENID" not in output
    assert "login.example.test" not in output


async def test_tencent_login_capture_rejects_callback_url_file_state_mismatch(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    closed: list[bool] = []

    class FakeService:
        async def fetch_qr(self) -> TencentAccountQRTicket:
            return TencentAccountQRTicket(
                provider=TencentLoginProvider.WECHAT,
                app_id="test-app",
                ticket="SECRET_EXPECTED_STATE",
                qr_url="https://open.weixin.qq.com/connect/qrconnect?state=SECRET_EXPECTED_STATE",
                device_id="0123456789abcdef0123456789abcdef",
            )

        def write_qr_png(self, ticket: TencentAccountQRTicket, output_path: Path) -> None:
            assert ticket.ticket == "SECRET_EXPECTED_STATE"
            output_path.write_bytes(b"PNG")

        def accept_oauth_callback(self, *, state: str, code: str) -> None:
            raise AssertionError(f"unexpected callback {state} {code}")

        async def query_qr(self, ticket: TencentAccountQRTicket) -> TencentAccountQRLoginStatus:
            raise AssertionError(f"query should not run after mismatched callback {ticket.ticket}")

        async def aclose(self) -> None:
            closed.append(True)

    callback_file = tmp_path / "oauth-callback.txt"
    callback_file.write_text(
        "https://login.example.test/wechat/callback?code=SECRET_CODE&state=SECRET_OTHER_STATE",
        encoding="utf-8",
    )
    qr_path = tmp_path / "tencent-login.png"

    with pytest.raises(TencentAccountQRLoginError, match="state mismatch") as exc_info:
        await main_module._capture_tencent_session_from_qr(
            FakeService(),
            qr_output_path=qr_path,
            timeout_seconds=1.0,
            poll_interval_seconds=0.01,
            callback_url_file=callback_file,
        )
    output = capsys.readouterr().out

    assert closed == [True]
    assert callback_file.exists()
    assert not qr_path.exists()
    assert "SECRET_EXPECTED_STATE" not in str(exc_info.value)
    assert "SECRET_OTHER_STATE" not in str(exc_info.value)
    assert "SECRET_CODE" not in str(exc_info.value)
    assert "SECRET_EXPECTED_STATE" not in output
    assert "SECRET_OTHER_STATE" not in output
    assert "SECRET_CODE" not in output


def _run_main(argv: list[str]) -> int:
    try:
        return main(argv)
    except SystemExit as exc:
        if isinstance(exc.code, int):
            return exc.code
        return 1
