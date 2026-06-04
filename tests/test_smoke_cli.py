import pytest

from qr_live_scanner_tencent.__main__ import main
from qr_live_scanner_tencent.interfaces import GameID
from qr_live_scanner_tencent.smoke import SmokeReport, SmokeTarget, build_smoke_report


def test_build_smoke_report_marks_p95_target_passed() -> None:
    target = SmokeTarget(
        platform="bilibili", room_id="12345", game_id=GameID.HONOR_OF_KINGS, uid="10001"
    )
    samples = [100.0 + index for index in range(50)]

    report = build_smoke_report(target, samples, target_p95_ms=800.0)

    assert report.sample_count == 50
    assert report.p95_ms < 800.0
    assert report.passed is True


def test_smoke_report_rejects_too_few_samples() -> None:
    target = SmokeTarget(
        platform="douyin", room_id="67890", game_id=GameID.HONOR_OF_KINGS, uid="10001"
    )

    with pytest.raises(ValueError, match="at least 50"):
        build_smoke_report(target, [100.0] * 49, target_p95_ms=800.0)


def test_smoke_report_rejects_non_positive_target() -> None:
    target = SmokeTarget(
        platform="bilibili", room_id="12345", game_id=GameID.HONOR_OF_KINGS, uid="10001"
    )

    with pytest.raises(ValueError, match="target P95"):
        build_smoke_report(target, [100.0] * 50, target_p95_ms=0.0)


def test_smoke_report_rejects_non_finite_latency_samples() -> None:
    target = SmokeTarget(
        platform="bilibili", room_id="12345", game_id=GameID.HONOR_OF_KINGS, uid="10001"
    )

    with pytest.raises(ValueError, match="finite"):
        build_smoke_report(target, [100.0] * 49 + [float("nan")], target_p95_ms=800.0)


def test_smoke_report_rejects_non_finite_target() -> None:
    target = SmokeTarget(
        platform="bilibili", room_id="12345", game_id=GameID.HONOR_OF_KINGS, uid="10001"
    )

    with pytest.raises(ValueError, match="target P95"):
        build_smoke_report(target, [100.0] * 50, target_p95_ms=float("inf"))


def test_smoke_report_text_omits_sensitive_values() -> None:
    target = SmokeTarget(
        platform="bilibili",
        room_id="12345?SESSDATA=SECRET_COOKIE_VALUE",
        game_id=GameID.HONOR_OF_KINGS,
        uid="10001",
    )
    report = SmokeReport(
        target=target,
        sample_count=50,
        p95_ms=123.4,
        min_ms=10.0,
        max_ms=200.0,
        target_p95_ms=800.0,
        passed=True,
    )

    text = report.to_text()

    assert "P95" in text
    assert "PASS" in text
    assert "room_id=[REDACTED]" in text
    assert "uid=[REDACTED]" in text
    assert "10001" not in text
    assert "12345" not in text
    assert "SECRET_COOKIE_VALUE" not in text
    assert "SESSDATA" not in text
    assert "token" not in text.lower()
    assert "cookie" not in text.lower()
    assert "payload" not in text.lower()


def test_smoke_report_cli_outputs_summary_without_sample_values(
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = [
        "smoke-report",
        "--platform",
        "bilibili",
        "--room-id",
        "12345",
        "--game-id",
        "honor_of_kings",
        "--uid",
        "10001",
        "--latencies-ms",
        ",".join(str(100 + index) for index in range(50)),
    ]

    exit_code = main(args)
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "sample_count=50" in output
    assert "P95" in output
    assert "uid=[REDACTED]" in output
    assert "10001" not in output
    assert "100,101" not in output


def test_smoke_report_cli_rejects_non_positive_target(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(
        [
            "smoke-report",
            "--platform",
            "bilibili",
            "--room-id",
            "12345",
            "--game-id",
            "honor_of_kings",
            "--uid",
            "10001",
            "--latencies-ms",
            ",".join(str(100 + index) for index in range(50)),
            "--target-p95-ms",
            "0",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "target P95" in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "payload" not in output.lower()


def test_smoke_report_cli_rejects_empty_latency_sample(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(
        [
            "smoke-report",
            "--platform",
            "bilibili",
            "--room-id",
            "12345",
            "--game-id",
            "honor_of_kings",
            "--uid",
            "10001",
            "--latencies-ms",
            "100,,101",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "[WARN]" in output
    assert "empty latency sample" in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "payload" not in output.lower()


def test_smoke_report_cli_rejects_non_finite_latency_sample(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(
        [
            "smoke-report",
            "--platform",
            "bilibili",
            "--room-id",
            "12345",
            "--game-id",
            "honor_of_kings",
            "--uid",
            "10001",
            "--latencies-ms",
            ",".join(["100"] * 49 + ["nan"]),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "[WARN]" in output
    assert "finite" in output.lower()
    assert "nan" not in output.lower()
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "payload" not in output.lower()


def test_smoke_report_cli_rejects_invalid_latency_sample_without_echoing_value(
    capsys: pytest.CaptureFixture[str],
) -> None:
    sensitive_sample = "SECRET_TOKEN_VALUE"

    exit_code = main(
        [
            "smoke-report",
            "--platform",
            "bilibili",
            "--room-id",
            "12345",
            "--game-id",
            "honor_of_kings",
            "--uid",
            "10001",
            "--latencies-ms",
            ",".join(["100"] * 49 + [sensitive_sample]),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "[WARN]" in output
    assert "invalid latency sample" in output
    assert sensitive_sample not in output
    assert "SECRET_TOKEN" not in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "payload" not in output.lower()


def test_smoke_report_cli_rejects_blank_room_id(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(
        [
            "smoke-report",
            "--platform",
            "bilibili",
            "--room-id",
            "   ",
            "--game-id",
            "honor_of_kings",
            "--uid",
            "10001",
            "--latencies-ms",
            ",".join(str(100 + index) for index in range(50)),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "[WARN]" in output
    assert "room id" in output.lower()
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "payload" not in output.lower()
