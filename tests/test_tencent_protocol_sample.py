import json
import subprocess
from pathlib import Path

import pytest

from qr_live_scanner_tencent.__main__ import main
from qr_live_scanner_tencent.accounts import (
    TencentAccountQRLoginError,
    load_tencent_account_qr_login_config,
)
from qr_live_scanner_tencent.interfaces import TencentLoginProvider
from qr_live_scanner_tencent.security import protocol_sample
from qr_live_scanner_tencent.security.protocol_sample import (
    build_tencent_protocol_sample_from_har,
    render_tencent_protocol_note,
)


def test_protocol_sample_keeps_redacted_har_shapes_without_values() -> None:
    har = {
        "log": {
            "entries": [
                {
                    "request": {
                        "method": "POST",
                        "url": (
                            "https://ssl.ptlogin2.qq.com/auth/qrcode/scan"
                            "?token=%5BREDACTED%5D&safe=keep"
                        ),
                        "headers": [
                            {"name": "Cookie", "value": "[REDACTED]"},
                            {"name": "User-Agent", "value": "Mozilla/5.0"},
                        ],
                        "postData": {"mimeType": "application/json", "text": "[REDACTED]"},
                    },
                    "response": {
                        "status": 200,
                        "headers": [{"name": "Set-Cookie", "value": "[REDACTED]"}],
                        "content": {"mimeType": "application/json", "text": "[REDACTED]"},
                    },
                }
            ]
        }
    }

    sample = build_tencent_protocol_sample_from_har(
        har,
        provider=TencentLoginProvider.QQ,
        flow="account-login",
    )
    encoded = json.dumps(sample, ensure_ascii=False)

    assert sample["source"] == "redacted-har"
    assert sample["provider"] == "qq"
    assert sample["flow"] == "account-login"
    assert sample["entries"] == [
        {
            "index": 0,
            "method": "POST",
            "scheme": "https",
            "host": "ssl.ptlogin2.qq.com",
            "path": "/auth/qrcode/scan",
            "query_keys": ["safe", "token"],
            "request_header_names": ["cookie", "user-agent"],
            "request_body_mime_type": "application/json",
            "has_request_body": True,
            "response_status": 200,
            "response_header_names": ["set-cookie"],
            "response_body_mime_type": "application/json",
        }
    ]
    assert "Mozilla/5.0" not in encoded
    assert "safe=keep" not in encoded
    assert "Cookie" not in encoded
    assert "[REDACTED]" not in encoded


def test_protocol_sample_rejects_raw_sensitive_har_without_echoing_values() -> None:
    secret = "SECRET_VALUE_DO_NOT_LEAK"
    har = {
        "log": {
            "entries": [
                {
                    "request": {
                        "method": "GET",
                        "url": f"https://ssl.ptlogin2.qq.com/auth/qrcode/scan?token={secret}",
                        "headers": [{"name": "Cookie", "value": f"uin={secret}"}],
                        "postData": {"text": secret},
                    },
                    "response": {"status": 200},
                }
            ]
        }
    }

    with pytest.raises(ValueError) as exc_info:
        build_tencent_protocol_sample_from_har(
            har,
            provider=TencentLoginProvider.QQ,
            flow="account-login",
        )

    message = str(exc_info.value)
    assert "redacted" in message.lower()
    assert secret not in message


def test_protocol_sample_cli_writes_summary_without_echoing_values(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_path = tmp_path / "tencent-login.redacted.har"
    output_path = tmp_path / "tencent-login.sample.json"
    input_path.write_text(
        json.dumps(
            {
                "log": {
                    "entries": [
                        {
                            "request": {
                                "method": "POST",
                                "url": (
                                    "https://ssl.ptlogin2.qq.com/auth/qrcode/query"
                                    "?ticket=%5BREDACTED%5D"
                                ),
                                "headers": [{"name": "Cookie", "value": "[REDACTED]"}],
                            },
                            "response": {"status": 200},
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "tencent-protocol-sample",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--provider",
            "qq",
            "--flow",
            "account-login",
        ]
    )
    output = capsys.readouterr().out
    saved = json.loads(output_path.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert "Tencent protocol sample written" in output
    assert saved["entries"][0]["query_keys"] == ["ticket"]
    assert "ticket=%5BREDACTED%5D" not in output_path.read_text(encoding="utf-8")
    assert "Cookie" not in output_path.read_text(encoding="utf-8")


def test_protocol_sample_cli_accepts_utf8_bom_redacted_har(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_path = tmp_path / "tencent-login.redacted.har"
    output_path = tmp_path / "tencent-login.sample.json"
    input_path.write_text(
        json.dumps(
            {
                "log": {
                    "entries": [
                        {
                            "request": {
                                "url": (
                                    "https://ssl.ptlogin2.qq.com/auth/qrcode/query"
                                    "?ticket=%5BREDACTED%5D"
                                ),
                                "headers": [],
                            },
                            "response": {"status": 200},
                        }
                    ]
                }
            }
        ),
        encoding="utf-8-sig",
    )

    exit_code = main(
        [
            "tencent-protocol-sample",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--provider",
            "qq",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert output_path.exists()
    assert "Unexpected UTF-8 BOM" not in output


def test_protocol_sample_cli_rejects_raw_har_without_writing_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "SECRET_VALUE_DO_NOT_LEAK"
    input_path = tmp_path / "tencent-login.har"
    output_path = tmp_path / "tencent-login.sample.json"
    input_path.write_text(
        json.dumps(
            {
                "log": {
                    "entries": [
                        {
                            "request": {
                                "url": f"https://ssl.ptlogin2.qq.com/auth/qrcode/query?ticket={secret}",
                                "headers": [{"name": "Cookie", "value": secret}],
                            },
                            "response": {"status": 200},
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "tencent-protocol-sample",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--provider",
            "qq",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert not output_path.exists()
    assert "redacted" in output.lower()
    assert secret not in output


def test_protocol_guide_cli_prints_safe_capture_workflow_without_secrets(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = _run_main(["tencent-protocol-guide", "--provider", "wechat"])
    output = capsys.readouterr().out
    lower_output = output.lower()

    assert exit_code == 0
    assert "Safe Tencent protocol capture workflow" in output
    assert "provider: wechat" in output
    assert "captures/tencent-login.har" in output
    assert "redact-har" in output
    assert "tencent-protocol-sample" in output
    assert "tencent-protocol-note" in output
    assert "tencent-protocol-config-skeleton" in output
    assert "tencent-protocol-artifact-check" in output
    assert "tencent-protocol-readiness" in output
    assert "validated_protocol = false" in output
    assert "Do not share raw HAR" in output
    assert "SECRET_VALUE_DO_NOT_LEAK" not in output
    assert "local-wechat-user" not in output
    assert "cookie:" not in lower_output
    assert "authorization:" not in lower_output
    assert "openid=" not in lower_output
    assert "qrsig=" not in lower_output
    assert "ticket=" not in lower_output


def test_protocol_preflight_cli_verifies_sensitive_paths_are_ignored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / ".gitignore").write_text("captures/\nprofiles/\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    exit_code = _run_main(["tencent-protocol-preflight"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "Tencent protocol preflight passed" in output
    assert "captures/ ignored" in output
    assert "profiles/ ignored" in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "ticket=" not in output.lower()


def test_protocol_preflight_cli_fails_when_sensitive_paths_are_not_ignored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / ".gitignore").write_text("work/\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    exit_code = _run_main(["tencent-protocol-preflight"])
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "Tencent protocol preflight failed" in output
    assert "captures/" in output
    assert "profiles/" in output
    assert "SECRET_VALUE_DO_NOT_LEAK" not in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "ticket=" not in output.lower()


def test_protocol_preflight_cli_fails_when_git_ignore_is_negated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / ".gitignore").write_text(
        "captures/\n!captures/\nprofiles/\n!profiles/\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    exit_code = _run_main(["tencent-protocol-preflight"])
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "Tencent protocol preflight failed" in output
    assert "captures/tencent-login.har" in output
    assert "profiles/tencent-account-login.toml" in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "ticket=" not in output.lower()


def test_protocol_preflight_cli_fails_when_sensitive_path_is_already_tracked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / ".gitignore").write_text("captures/\nprofiles/\n", encoding="utf-8")
    capture_path = tmp_path / "captures" / "tencent-login.har"
    capture_path.parent.mkdir(parents=True)
    capture_path.write_text("SECRET_VALUE_DO_NOT_LEAK", encoding="utf-8")
    subprocess.run(["git", "add", "-f", "captures/tencent-login.har"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)

    exit_code = _run_main(["tencent-protocol-preflight"])
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "Tencent protocol preflight failed" in output
    assert "already tracked" in output
    assert "captures/tencent-login.har" in output
    assert "SECRET_VALUE_DO_NOT_LEAK" not in output
    assert "cookie" not in output.lower()
    assert "ticket=" not in output.lower()


def test_protocol_note_renders_validation_checklist_without_values() -> None:
    sample = {
        "source": "redacted-har",
        "provider": "qq",
        "flow": "account-login",
        "entries": [
            {
                "index": 0,
                "method": "POST",
                "scheme": "https",
                "host": "ssl.ptlogin2.qq.com",
                "path": "/auth/qrcode/query",
                "query_keys": ["safe", "ticket"],
                "request_header_names": ["cookie", "user-agent"],
                "request_body_mime_type": "application/json",
                "has_request_body": True,
                "response_status": 200,
                "response_header_names": ["set-cookie"],
                "response_body_mime_type": "application/json",
            }
        ],
    }

    note = render_tencent_protocol_note(sample)

    assert "# Tencent Protocol Validation Note" in note
    assert "Provider: `qq`" in note
    assert "Flow: `account-login`" in note
    assert "`POST`" in note
    assert "`ssl.ptlogin2.qq.com`" in note
    assert "`/auth/qrcode/query`" in note
    assert "`safe`, `ticket`" in note
    assert "QR payload shape" in note
    assert "success condition" in note
    assert "safe=keep" not in note
    assert "SECRET_VALUE_DO_NOT_LEAK" not in note
    assert "[REDACTED]" not in note


def test_protocol_note_cli_writes_markdown_without_sensitive_values(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sample_path = tmp_path / "tencent-login.sample.json"
    output_path = tmp_path / "tencent-login.note.md"
    sample_path.write_text(
        json.dumps(
            {
                "source": "redacted-har",
                "provider": "qq",
                "flow": "account-login",
                "entries": [
                    {
                        "index": 0,
                        "method": "GET",
                        "scheme": "https",
                        "host": "ssl.ptlogin2.qq.com",
                        "path": "/auth/qrcode/query",
                        "query_keys": ["ticket"],
                        "request_header_names": ["cookie"],
                        "request_body_mime_type": "",
                        "has_request_body": False,
                        "response_status": 200,
                        "response_header_names": [],
                        "response_body_mime_type": "",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "tencent-protocol-note",
            "--input",
            str(sample_path),
            "--output",
            str(output_path),
        ]
    )
    output = capsys.readouterr().out
    note = output_path.read_text(encoding="utf-8")

    assert exit_code == 0
    assert "Tencent protocol note written" in output
    assert "Endpoint Shapes" in note
    assert "`ticket`" in note
    assert "ticket=%5BREDACTED%5D" not in note
    assert "Cookie:" not in note


def test_protocol_note_cli_rejects_invalid_sample_without_writing_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sample_path = tmp_path / "bad.sample.json"
    output_path = tmp_path / "bad.note.md"
    sample_path.write_text(
        json.dumps(
            {
                "source": "redacted-har",
                "provider": "qq",
                "flow": "account-login",
                "entries": [{"method": "GET", "host": "ssl.ptlogin2.qq.com"}],
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "tencent-protocol-note",
            "--input",
            str(sample_path),
            "--output",
            str(output_path),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert not output_path.exists()
    assert "protocol sample" in output.lower()


def test_account_qr_config_skeleton_uses_safe_defaults_without_sensitive_values(
    tmp_path: Path,
) -> None:
    sample = {
        "source": "redacted-har",
        "provider": "qq",
        "flow": "account-login",
        "entries": [
            {
                "index": 0,
                "method": "GET",
                "scheme": "https",
                "host": "ssl.ptlogin2.qq.com",
                "path": "/ptqrshow",
                "query_keys": ["appid", "token"],
                "request_header_names": ["cookie", "user-agent"],
                "request_body_mime_type": "",
                "has_request_body": False,
                "response_status": 200,
                "response_header_names": [],
                "response_body_mime_type": "image/png",
            },
            {
                "index": 1,
                "method": "GET",
                "scheme": "https",
                "host": "ssl.ptlogin2.qq.com",
                "path": "/ptqrlogin",
                "query_keys": ["qrsig", "ticket"],
                "request_header_names": ["cookie"],
                "request_body_mime_type": "",
                "has_request_body": False,
                "response_status": 200,
                "response_header_names": [],
                "response_body_mime_type": "application/javascript",
            },
        ],
    }

    assert hasattr(protocol_sample, "render_tencent_account_qr_config_skeleton")
    skeleton = protocol_sample.render_tencent_account_qr_config_skeleton(sample)
    lower_skeleton = skeleton.lower()

    assert "[account_qr_login.qq]" in skeleton
    assert "validated_protocol = false" in skeleton
    assert 'fetch_url = "https://ssl.ptlogin2.qq.com/ptqrshow"' in skeleton
    assert 'query_url = "https://ssl.ptlogin2.qq.com/ptqrlogin"' in skeleton
    assert 'app_id = "TODO-verified-app-id"' in skeleton
    assert "?" not in skeleton
    assert "#" not in skeleton
    assert "[REDACTED]" not in skeleton
    assert "token" not in lower_skeleton
    assert "ticket" not in lower_skeleton
    assert "cookie" not in lower_skeleton
    assert "qrsig" not in lower_skeleton

    config_path = tmp_path / "tencent-account-login.toml"
    config_path.write_text(skeleton, encoding="utf-8")
    with pytest.raises(TencentAccountQRLoginError) as exc_info:
        load_tencent_account_qr_login_config(config_path, TencentLoginProvider.QQ)

    assert "not validated" in str(exc_info.value)


def test_account_qr_config_skeleton_cli_writes_safe_toml(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sample_path = tmp_path / "tencent-login.sample.json"
    output_path = tmp_path / "tencent-account-login.toml"
    sample_path.write_text(
        json.dumps(
            {
                "source": "redacted-har",
                "provider": "wechat",
                "flow": "account-login",
                "entries": [
                    {
                        "index": 0,
                        "method": "POST",
                        "scheme": "https",
                        "host": "open.weixin.qq.com",
                        "path": "/connect/qrconnect",
                        "query_keys": ["appid", "redirect_uri"],
                        "request_header_names": ["cookie"],
                        "request_body_mime_type": "",
                        "has_request_body": False,
                        "response_status": 200,
                        "response_header_names": [],
                        "response_body_mime_type": "text/html",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = _run_main(
        [
            "tencent-protocol-config-skeleton",
            "--input",
            str(sample_path),
            "--output",
            str(output_path),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert output_path.exists()
    assert "Tencent protocol config skeleton written" in output
    skeleton = output_path.read_text(encoding="utf-8")
    assert "[account_qr_login.wechat]" in skeleton
    assert "validated_protocol = false" in skeleton
    assert "open.weixin.qq.com/connect/qrconnect" in skeleton
    assert "?" not in skeleton
    assert "cookie" not in skeleton.lower()


def test_account_qr_config_skeleton_cli_rejects_invalid_sample_without_writing_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sample_path = tmp_path / "bad.sample.json"
    output_path = tmp_path / "bad.toml"
    sample_path.write_text(
        json.dumps(
            {
                "source": "redacted-har",
                "provider": "qq",
                "flow": "game-scan-confirm",
                "entries": [],
            }
        ),
        encoding="utf-8",
    )

    exit_code = _run_main(
        [
            "tencent-protocol-config-skeleton",
            "--input",
            str(sample_path),
            "--output",
            str(output_path),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert not output_path.exists()
    assert "protocol sample" in output.lower()


def test_protocol_artifact_check_cli_accepts_safe_sample_and_config(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sample_path = tmp_path / "tencent-login.sample.json"
    config_path = tmp_path / "tencent-account-login.toml"
    sample_path.write_text(
        json.dumps(
            {
                "source": "redacted-har",
                "provider": "qq",
                "flow": "account-login",
                "entries": [
                    {
                        "index": 0,
                        "method": "GET",
                        "scheme": "https",
                        "host": "ssl.ptlogin2.qq.com",
                        "path": "/ptqrshow",
                        "query_keys": ["appid"],
                        "request_header_names": ["user-agent"],
                        "request_body_mime_type": "",
                        "has_request_body": False,
                        "response_status": 200,
                        "response_header_names": [],
                        "response_body_mime_type": "image/png",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    config_path.write_text(
        "\n".join(
            [
                "[account_qr_login.qq]",
                "validated_protocol = false",
                'fetch_url = "https://ssl.ptlogin2.qq.com/ptqrshow"',
                'query_url = "https://ssl.ptlogin2.qq.com/ptqrlogin"',
                'app_id = "TODO-verified-app-id"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    exit_code = _run_main(
        [
            "tencent-protocol-artifact-check",
            "--sample",
            str(sample_path),
            "--config",
            str(config_path),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "Tencent protocol artifacts passed" in output
    assert "provider=qq" in output
    assert "validated_protocol = false" in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "ticket=" not in output.lower()


def test_protocol_artifact_check_cli_rejects_validated_config_without_echoing_values(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "SECRET_VALUE_DO_NOT_LEAK"
    sample_path = tmp_path / "tencent-login.sample.json"
    config_path = tmp_path / "tencent-account-login.toml"
    _write_safe_protocol_sample(sample_path)
    config_path.write_text(
        "\n".join(
            [
                "[account_qr_login.qq]",
                "validated_protocol = true",
                f'fetch_url = "https://ssl.ptlogin2.qq.com/ptqrshow/{secret}"',
                'query_url = "https://ssl.ptlogin2.qq.com/ptqrlogin"',
                'app_id = "TODO-verified-app-id"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    exit_code = _run_main(
        [
            "tencent-protocol-artifact-check",
            "--sample",
            str(sample_path),
            "--config",
            str(config_path),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "Tencent protocol artifact check failed" in output
    assert "validated_protocol" in output
    assert secret not in output


def test_protocol_artifact_check_cli_rejects_signed_config_url_without_echoing_values(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "SECRET_VALUE_DO_NOT_LEAK"
    sample_path = tmp_path / "tencent-login.sample.json"
    config_path = tmp_path / "tencent-account-login.toml"
    _write_safe_protocol_sample(sample_path)
    config_path.write_text(
        "\n".join(
            [
                "[account_qr_login.qq]",
                "validated_protocol = false",
                f'fetch_url = "https://ssl.ptlogin2.qq.com/ptqrshow?token={secret}"',
                'query_url = "https://ssl.ptlogin2.qq.com/ptqrlogin"',
                'app_id = "TODO-verified-app-id"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    exit_code = _run_main(
        [
            "tencent-protocol-artifact-check",
            "--sample",
            str(sample_path),
            "--config",
            str(config_path),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "Tencent protocol artifact check failed" in output
    assert "endpoint" in output.lower()
    assert secret not in output


def test_protocol_artifact_check_cli_rejects_raw_sample_values_without_echoing_values(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "SECRET_VALUE_DO_NOT_LEAK"
    sample_path = tmp_path / "tencent-login.sample.json"
    config_path = tmp_path / "tencent-account-login.toml"
    _write_safe_protocol_config(config_path)
    sample_path.write_text(
        json.dumps(
            {
                "source": "redacted-har",
                "provider": "qq",
                "flow": "account-login",
                "entries": [
                    {
                        "index": 0,
                        "method": "GET",
                        "scheme": "https",
                        "host": "ssl.ptlogin2.qq.com",
                        "path": "/ptqrshow",
                        "query_keys": ["appid"],
                        "request_header_names": ["user-agent"],
                        "request_header_values": [secret],
                        "request_body_mime_type": "",
                        "has_request_body": False,
                        "response_status": 200,
                        "response_header_names": [],
                        "response_body_mime_type": "image/png",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = _run_main(
        [
            "tencent-protocol-artifact-check",
            "--sample",
            str(sample_path),
            "--config",
            str(config_path),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "Tencent protocol artifact check failed" in output
    assert "protocol sample" in output.lower()
    assert secret not in output


def test_protocol_readiness_cli_blocks_unchecked_validation_note(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sample_path = tmp_path / "tencent-login.sample.json"
    config_path = tmp_path / "tencent-account-login.toml"
    note_path = tmp_path / "tencent-login.note.md"
    _write_safe_protocol_sample(sample_path)
    _write_safe_protocol_config(config_path)
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    note_path.write_text(render_tencent_protocol_note(sample), encoding="utf-8")

    exit_code = _run_main(
        [
            "tencent-protocol-readiness",
            "--sample",
            str(sample_path),
            "--config",
            str(config_path),
            "--note",
            str(note_path),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "Tencent protocol readiness blocked" in output
    assert "checked=0/8" in output
    assert "real_http=disabled" in output
    assert "SECRET_VALUE_DO_NOT_LEAK" not in output
    assert "ticket=" not in output.lower()


def test_protocol_readiness_cli_accepts_complete_validation_note(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sample_path = tmp_path / "tencent-login.sample.json"
    config_path = tmp_path / "tencent-account-login.toml"
    note_path = tmp_path / "tencent-login.note.md"
    _write_safe_protocol_sample(sample_path)
    _write_safe_protocol_config(config_path)
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    note_path.write_text(
        render_tencent_protocol_note(sample).replace("- [ ] ", "- [x] "),
        encoding="utf-8",
    )

    exit_code = _run_main(
        [
            "tencent-protocol-readiness",
            "--sample",
            str(sample_path),
            "--config",
            str(config_path),
            "--note",
            str(note_path),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "Tencent protocol readiness passed" in output
    assert "provider=qq" in output
    assert "checked=8/8" in output
    assert "real_http=disabled" in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "ticket=" not in output.lower()


def test_protocol_readiness_cli_rejects_unsafe_artifacts_without_echoing_values(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "SECRET_VALUE_DO_NOT_LEAK"
    sample_path = tmp_path / "tencent-login.sample.json"
    config_path = tmp_path / "tencent-account-login.toml"
    note_path = tmp_path / "tencent-login.note.md"
    _write_safe_protocol_sample(sample_path)
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    note_path.write_text(
        render_tencent_protocol_note(sample).replace("- [ ] ", "- [x] "),
        encoding="utf-8",
    )
    config_path.write_text(
        "\n".join(
            [
                "[account_qr_login.qq]",
                "validated_protocol = true",
                f'fetch_url = "https://ssl.ptlogin2.qq.com/ptqrshow?ticket={secret}"',
                'query_url = "https://ssl.ptlogin2.qq.com/ptqrlogin"',
                'app_id = "TODO-verified-app-id"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    exit_code = _run_main(
        [
            "tencent-protocol-readiness",
            "--sample",
            str(sample_path),
            "--config",
            str(config_path),
            "--note",
            str(note_path),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "Tencent protocol readiness failed" in output
    assert "validated_protocol" in output
    assert secret not in output


def test_protocol_readiness_cli_rejects_sensitive_note_values_without_echoing_values(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "SECRET_VALUE_DO_NOT_LEAK"
    sample_path = tmp_path / "tencent-login.sample.json"
    config_path = tmp_path / "tencent-account-login.toml"
    note_path = tmp_path / "tencent-login.note.md"
    _write_safe_protocol_sample(sample_path)
    _write_safe_protocol_config(config_path)
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    note_path.write_text(
        "\n".join(
            [
                render_tencent_protocol_note(sample).replace("- [ ] ", "- [x] "),
                "",
                "Leaked local URL:",
                f"https://ssl.ptlogin2.qq.com/ptqrlogin?ticket={secret}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    exit_code = _run_main(
        [
            "tencent-protocol-readiness",
            "--sample",
            str(sample_path),
            "--config",
            str(config_path),
            "--note",
            str(note_path),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "Tencent protocol readiness failed" in output
    assert "protocol note" in output.lower()
    assert secret not in output


def _write_safe_protocol_sample(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "source": "redacted-har",
                "provider": "qq",
                "flow": "account-login",
                "entries": [
                    {
                        "index": 0,
                        "method": "GET",
                        "scheme": "https",
                        "host": "ssl.ptlogin2.qq.com",
                        "path": "/ptqrshow",
                        "query_keys": ["appid"],
                        "request_header_names": ["user-agent"],
                        "request_body_mime_type": "",
                        "has_request_body": False,
                        "response_status": 200,
                        "response_header_names": [],
                        "response_body_mime_type": "image/png",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _write_safe_protocol_config(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "[account_qr_login.qq]",
                "validated_protocol = false",
                'fetch_url = "https://ssl.ptlogin2.qq.com/ptqrshow"',
                'query_url = "https://ssl.ptlogin2.qq.com/ptqrlogin"',
                'app_id = "TODO-verified-app-id"',
                "",
            ]
        ),
        encoding="utf-8",
    )


def _run_main(argv: list[str]) -> int:
    try:
        return main(argv)
    except SystemExit as exc:
        if isinstance(exc.code, int):
            return exc.code
        return 1


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=path, check=True)
