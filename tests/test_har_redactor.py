import json
from pathlib import Path

import pytest

from qr_live_scanner_tencent.__main__ import main
from qr_live_scanner_tencent.security.har import redact_har


def test_redact_har_removes_sensitive_headers_query_and_body_text() -> None:
    secret = "SECRET_VALUE_DO_NOT_LEAK"
    har = {
        "log": {
            "entries": [
                {
                    "request": {
                        "method": "POST",
                        "url": (
                            "https://ssl.ptlogin2.qq.com/auth/qrcode/scan"
                            f"?token={secret}&uid=10001&safe=keep"
                        ),
                        "headers": [
                            {"name": "Authorization", "value": f"Bearer {secret}"},
                            {"name": "Cookie", "value": f"SESSDATA={secret}"},
                            {"name": "User-Agent", "value": "Mozilla/5.0"},
                        ],
                        "queryString": [
                            {"name": "token", "value": secret},
                            {"name": "uid", "value": "10001"},
                            {"name": "safe", "value": "keep"},
                        ],
                        "postData": {"mimeType": "application/json", "text": secret},
                    },
                    "response": {
                        "status": 200,
                        "headers": [{"name": "Set-Cookie", "value": f"stoken={secret}"}],
                        "content": {"mimeType": "application/json", "text": secret},
                    },
                }
            ]
        }
    }

    redacted = redact_har(har)
    encoded = json.dumps(redacted, ensure_ascii=False)

    assert secret not in encoded
    assert "10001" not in encoded
    assert "ssl.ptlogin2.qq.com/auth/qrcode/scan" in encoded
    assert "safe=keep" in encoded
    assert redacted["log"]["entries"][0]["request"]["headers"][2]["value"] == "Mozilla/5.0"


def test_redact_har_removes_sensitive_url_path_segments() -> None:
    secret = "SECRET_TICKET_PATH_VALUE"
    opaque = "abc123def456ghi789"
    har = {
        "log": {
            "entries": [
                {
                    "request": {
                        "url": (
                            "https://ssl.ptlogin2.qq.com/auth/qrcode"
                            f"/{secret}/uid/10001/device/{opaque}/scan?safe=keep"
                        ),
                        "headers": [],
                    },
                    "response": {"status": 200},
                }
            ]
        }
    }

    redacted = redact_har(har)
    encoded = json.dumps(redacted, ensure_ascii=False)

    assert secret not in encoded
    assert opaque not in encoded
    assert "10001" not in encoded
    assert "auth/qrcode" in encoded
    assert "safe=keep" in encoded


def test_redact_har_cli_writes_sanitized_copy_without_echoing_secrets(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "SECRET_VALUE_DO_NOT_LEAK"
    input_path = tmp_path / "capture.har"
    output_path = tmp_path / "capture.redacted.har"
    input_path.write_text(
        json.dumps(
            {
                "log": {
                    "entries": [
                        {
                            "request": {
                                "url": (
                                    "https://ssl.ptlogin2.qq.com/auth/qrcode/confirm"
                                    f"?scan_token={secret}"
                                ),
                                "headers": [{"name": "Cookie", "value": secret}],
                            },
                            "response": {"content": {"text": secret}},
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(["redact-har", "--input", str(input_path), "--output", str(output_path)])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert output_path.exists()
    assert secret not in output
    assert secret not in output_path.read_text(encoding="utf-8")
    assert "confirm" in output_path.read_text(encoding="utf-8")


def test_redact_har_removes_tencent_specific_token_fields() -> None:
    secret = "SECRET_VALUE_DO_NOT_LEAK"
    har = {
        "log": {
            "entries": [
                {
                    "request": {
                        "url": "https://ssl.ptlogin2.qq.com/auth/qrcode/scan",
                        "headers": [{"name": "X-Rpc-Device_Id", "value": "device"}],
                        "postData": {
                            "params": [
                                {"name": "access_token", "value": secret},
                                {"name": "qrsig", "value": secret},
                                {"name": "account_id", "value": secret},
                                {"name": "mid", "value": secret},
                                {"name": "stuid", "value": "10001"},
                            ]
                        },
                    },
                    "response": {"content": {"decodedBodySize": 0}},
                }
            ]
        }
    }

    encoded = json.dumps(redact_har(har), ensure_ascii=False)

    assert secret not in encoded
    assert "10001" not in encoded
    assert "device" in encoded


def test_redact_har_cli_rejects_same_input_and_output_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    input_path = tmp_path / "capture.har"
    input_path.write_text('{"log":{"entries":[]}}', encoding="utf-8")

    exit_code = main(["redact-har", "--input", str(input_path), "--output", str(input_path)])
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "input and output" in output.lower()
    assert input_path.read_text(encoding="utf-8") == '{"log":{"entries":[]}}'


def test_redact_har_cli_rejects_invalid_har_structure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    input_path = tmp_path / "capture.har"
    output_path = tmp_path / "capture.redacted.har"
    input_path.write_text('{"log":{"entries":[{"request":{}}]}}', encoding="utf-8")

    exit_code = main(["redact-har", "--input", str(input_path), "--output", str(output_path)])
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "har" in output.lower()
    assert not output_path.exists()
