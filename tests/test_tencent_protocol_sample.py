import json
from pathlib import Path

import pytest

from qr_live_scanner_tencent.__main__ import main
from qr_live_scanner_tencent.interfaces import TencentLoginProvider
from qr_live_scanner_tencent.security.protocol_sample import (
    build_tencent_protocol_sample_from_har,
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
