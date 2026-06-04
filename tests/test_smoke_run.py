import pytest

import qr_live_scanner_tencent.smoke as smoke_module
from qr_live_scanner_tencent.__main__ import main
from qr_live_scanner_tencent.interfaces import (
    DEFAULT_AGGRESSIVE_ROI,
    AuthMode,
    GameID,
    ROIConfig,
    StreamResolveError,
)
from qr_live_scanner_tencent.smoke import (
    DecodeProbeResult,
    SmokeReport,
    SmokeTarget,
    run_synthetic_smoke,
)


@pytest.mark.asyncio
async def test_run_synthetic_smoke_collects_report_samples() -> None:
    target = SmokeTarget(
        platform="synthetic", room_id="local", game_id=GameID.HONOR_OF_KINGS, uid="10001"
    )

    report = await run_synthetic_smoke(target, sample_count=50, target_p95_ms=800.0)

    assert report.sample_count == 50
    assert report.passed is True
    assert report.p95_ms < 800.0


def test_smoke_run_cli_rejects_douyin_real_mode_without_signed_url_factory(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(
        [
            "smoke-run",
            "--mode",
            "real",
            "--platform",
            "douyin",
            "--room-id",
            "12345",
            "--game-id",
            "honor_of_kings",
            "--uid",
            "10001",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "Douyin" in output
    assert "signed" in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "payload" not in output.lower()


def test_smoke_run_cli_uses_bilibili_decode_only_runner(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fake_runner(
        target: SmokeTarget,
        *,
        auth_mode: AuthMode,
        roi: ROIConfig,
        sample_count: int,
        target_p95_ms: float,
        max_wait_seconds: float,
        cookie: str | None,
        enable_pyzbar_fallback: bool,
        enable_roi_fallback: bool,
    ) -> SmokeReport:
        assert target.platform == "bilibili"
        assert target.room_id == "12345"
        assert auth_mode is AuthMode.COOKIE
        assert roi == ROIConfig(x=0.1, y=0.2, width=0.3, height=0.4)
        assert sample_count == 50
        assert max_wait_seconds == 12.5
        assert cookie == "FAKE_COOKIE"
        assert enable_pyzbar_fallback is False
        assert enable_roi_fallback is True
        return SmokeReport(
            target=target,
            sample_count=sample_count,
            p95_ms=120.0,
            min_ms=100.0,
            max_ms=140.0,
            target_p95_ms=target_p95_ms,
            passed=True,
        )

    monkeypatch.setattr(smoke_module, "run_bilibili_decode_smoke", fake_runner)
    monkeypatch.setenv("QR_LIVE_SCANNER_TENCENT_BILIBILI_COOKIE", "FAKE_COOKIE")

    exit_code = main(
        [
            "smoke-run",
            "--mode",
            "real",
            "--platform",
            "bilibili",
            "--room-id",
            "12345",
            "--game-id",
            "honor_of_kings",
            "--uid",
            "10001",
            "--sample-count",
            "50",
            "--target-p95-ms",
            "800",
            "--auth-mode",
            "cookie",
            "--roi",
            "0.1,0.2,0.3,0.4",
            "--max-wait-seconds",
            "12.5",
            "--cookie-env",
            "QR_LIVE_SCANNER_TENCENT_BILIBILI_COOKIE",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "sample_count=50" in output
    assert "P95=120.00ms" in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "payload" not in output.lower()


def test_smoke_run_cli_uses_douyin_browser_runner_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fake_runner(
        target: SmokeTarget,
        *,
        auth_mode: AuthMode,
        roi: ROIConfig,
        sample_count: int,
        target_p95_ms: float,
        max_wait_seconds: float,
        user_data_dir: str,
        chrome_executable_path: str | None,
        enable_pyzbar_fallback: bool,
        enable_roi_fallback: bool,
    ) -> SmokeReport:
        assert target.platform == "douyin"
        assert target.room_id == "https://live.douyin.com/335354047186"
        assert auth_mode is AuthMode.AUTO
        assert roi == DEFAULT_AGGRESSIVE_ROI
        assert sample_count == 50
        assert target_p95_ms == 800.0
        assert max_wait_seconds == 12.0
        assert user_data_dir == "profiles/douyin"
        assert chrome_executable_path is None
        assert enable_pyzbar_fallback is False
        assert enable_roi_fallback is True
        return SmokeReport(
            target=target,
            sample_count=sample_count,
            p95_ms=120.0,
            min_ms=100.0,
            max_ms=140.0,
            target_p95_ms=target_p95_ms,
            passed=True,
        )

    monkeypatch.setattr(smoke_module, "run_douyin_browser_decode_smoke", fake_runner)

    exit_code = main(
        [
            "smoke-run",
            "--mode",
            "real",
            "--platform",
            "douyin",
            "--room-id",
            "https://live.douyin.com/335354047186",
            "--game-id",
            "honor_of_kings",
            "--uid",
            "10001",
            "--sample-count",
            "50",
            "--target-p95-ms",
            "800",
            "--max-wait-seconds",
            "12",
            "--browser-resolver",
            "--browser-user-data-dir",
            "profiles/douyin",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "sample_count=50" in output
    assert "335354047186" not in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "payload" not in output.lower()


def test_smoke_run_cli_can_enable_pyzbar_fallback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fake_runner(
        target: SmokeTarget,
        *,
        auth_mode: AuthMode,
        roi: ROIConfig,
        sample_count: int,
        target_p95_ms: float,
        max_wait_seconds: float,
        cookie: str | None,
        enable_pyzbar_fallback: bool,
        enable_roi_fallback: bool,
    ) -> SmokeReport:
        assert enable_pyzbar_fallback is True
        assert enable_roi_fallback is True
        return SmokeReport(
            target=target,
            sample_count=sample_count,
            p95_ms=120.0,
            min_ms=100.0,
            max_ms=140.0,
            target_p95_ms=target_p95_ms,
            passed=True,
        )

    monkeypatch.setattr(smoke_module, "run_bilibili_decode_smoke", fake_runner)

    exit_code = main(
        [
            "smoke-run",
            "--mode",
            "real",
            "--platform",
            "bilibili",
            "--room-id",
            "12345",
            "--game-id",
            "honor_of_kings",
            "--uid",
            "10001",
            "--sample-count",
            "50",
            "--enable-pyzbar-fallback",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "sample_count=50" in output
    assert "payload" not in output.lower()


def test_decode_probe_cli_uses_single_sample_bilibili_runner(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fake_probe_runner(
        target: SmokeTarget,
        *,
        auth_mode: AuthMode,
        roi: ROIConfig,
        sample_count: int,
        max_wait_seconds: float,
        cookie: str | None,
        enable_pyzbar_fallback: bool,
        enable_roi_fallback: bool,
    ) -> DecodeProbeResult:
        assert target.platform == "bilibili"
        assert target.room_id == "12345"
        assert auth_mode is AuthMode.ANONYMOUS
        assert roi == ROIConfig(x=0.1, y=0.2, width=0.3, height=0.4)
        assert sample_count == 1
        assert max_wait_seconds == 5.0
        assert cookie is None
        assert enable_pyzbar_fallback is False
        assert enable_roi_fallback is True
        return DecodeProbeResult(
            target=target,
            sample_count=1,
            first_latency_ms=12.0,
            min_latency_ms=12.0,
            max_latency_ms=12.0,
        )

    monkeypatch.setattr(smoke_module, "run_bilibili_decode_probe", fake_probe_runner)

    exit_code = main(
        [
            "decode-probe",
            "--platform",
            "bilibili",
            "--room-id",
            "12345",
            "--game-id",
            "honor_of_kings",
            "--uid",
            "10001",
            "--auth-mode",
            "anonymous",
            "--roi",
            "0.1,0.2,0.3,0.4",
            "--max-wait-seconds",
            "5",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "decode probe" in output.lower()
    assert "sample_count=1" in output
    assert "payload" not in output.lower()
    assert "10001" not in output


def test_decode_probe_cli_rejects_non_positive_sample_count_before_runner(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def unexpected_probe_runner(
        target: SmokeTarget,
        *,
        auth_mode: AuthMode,
        roi: ROIConfig,
        sample_count: int,
        max_wait_seconds: float,
        cookie: str | None,
        enable_pyzbar_fallback: bool,
        enable_roi_fallback: bool,
    ) -> DecodeProbeResult:
        raise AssertionError("probe runner should not be called")

    monkeypatch.setattr(smoke_module, "run_bilibili_decode_probe", unexpected_probe_runner)

    exit_code = main(
        [
            "decode-probe",
            "--platform",
            "bilibili",
            "--room-id",
            "12345",
            "--game-id",
            "honor_of_kings",
            "--uid",
            "10001",
            "--sample-count",
            "0",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "[WARN]" in output
    assert "sample count" in output.lower()
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "payload" not in output.lower()


def test_decode_probe_cli_can_enable_pyzbar_fallback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fake_probe_runner(
        target: SmokeTarget,
        *,
        auth_mode: AuthMode,
        roi: ROIConfig,
        sample_count: int,
        max_wait_seconds: float,
        cookie: str | None,
        enable_pyzbar_fallback: bool,
        enable_roi_fallback: bool,
    ) -> DecodeProbeResult:
        assert enable_pyzbar_fallback is True
        assert enable_roi_fallback is True
        return DecodeProbeResult(
            target=target,
            sample_count=sample_count,
            first_latency_ms=12.0,
            min_latency_ms=12.0,
            max_latency_ms=12.0,
        )

    monkeypatch.setattr(smoke_module, "run_bilibili_decode_probe", fake_probe_runner)

    exit_code = main(
        [
            "decode-probe",
            "--platform",
            "bilibili",
            "--room-id",
            "12345",
            "--game-id",
            "honor_of_kings",
            "--uid",
            "10001",
            "--enable-pyzbar-fallback",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "sample_count=1" in output
    assert "payload" not in output.lower()


def test_decode_probe_cli_defaults_to_aggressive_roi_and_fallback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fake_probe_runner(
        target: SmokeTarget,
        *,
        auth_mode: AuthMode,
        roi: ROIConfig,
        sample_count: int,
        max_wait_seconds: float,
        cookie: str | None,
        enable_pyzbar_fallback: bool,
        enable_roi_fallback: bool,
    ) -> DecodeProbeResult:
        assert roi == DEFAULT_AGGRESSIVE_ROI
        assert enable_roi_fallback is True
        return DecodeProbeResult(
            target=target,
            sample_count=sample_count,
            first_latency_ms=6.0,
            min_latency_ms=6.0,
            max_latency_ms=6.0,
        )

    monkeypatch.setattr(smoke_module, "run_bilibili_decode_probe", fake_probe_runner)

    exit_code = main(
        [
            "decode-probe",
            "--platform",
            "bilibili",
            "--room-id",
            "12345",
            "--game-id",
            "honor_of_kings",
            "--uid",
            "10001",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "sample_count=1" in output
    assert "payload" not in output.lower()


def test_decode_probe_cli_rejects_douyin_without_signed_url_factory(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(
        [
            "decode-probe",
            "--platform",
            "douyin",
            "--room-id",
            "SECRET_ROOM_ID",
            "--game-id",
            "honor_of_kings",
            "--uid",
            "10001",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "Douyin" in output
    assert "signed" in output
    assert "SECRET_ROOM_ID" not in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "payload" not in output.lower()


def test_decode_probe_cli_uses_douyin_browser_runner_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fake_probe_runner(
        target: SmokeTarget,
        *,
        auth_mode: AuthMode,
        roi: ROIConfig,
        sample_count: int,
        max_wait_seconds: float,
        user_data_dir: str,
        chrome_executable_path: str | None,
        enable_pyzbar_fallback: bool,
        enable_roi_fallback: bool,
    ) -> DecodeProbeResult:
        assert target.platform == "douyin"
        assert auth_mode is AuthMode.AUTO
        assert roi == DEFAULT_AGGRESSIVE_ROI
        assert sample_count == 1
        assert max_wait_seconds == 10.0
        assert user_data_dir == "profiles/douyin"
        assert chrome_executable_path is None
        assert enable_pyzbar_fallback is False
        assert enable_roi_fallback is True
        return DecodeProbeResult(
            target=target,
            sample_count=sample_count,
            first_latency_ms=18.0,
            min_latency_ms=18.0,
            max_latency_ms=18.0,
        )

    monkeypatch.setattr(smoke_module, "run_douyin_browser_decode_probe", fake_probe_runner)

    exit_code = main(
        [
            "decode-probe",
            "--platform",
            "douyin",
            "--room-id",
            "335354047186",
            "--game-id",
            "honor_of_kings",
            "--uid",
            "10001",
            "--max-wait-seconds",
            "10",
            "--browser-resolver",
            "--browser-user-data-dir",
            "profiles/douyin",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "decode probe" in output.lower()
    assert "sample_count=1" in output
    assert "335354047186" not in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "payload" not in output.lower()


def test_decode_probe_cli_rejects_cookie_env_outside_cookie_auth_without_echoing_value(
    capsys: pytest.CaptureFixture[str],
) -> None:
    misplaced_cookie = "SESSDATA=SECRET_COOKIE_VALUE"

    exit_code = main(
        [
            "decode-probe",
            "--platform",
            "bilibili",
            "--room-id",
            "12345",
            "--game-id",
            "honor_of_kings",
            "--uid",
            "10001",
            "--auth-mode",
            "anonymous",
            "--cookie-env",
            misplaced_cookie,
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "[WARN]" in output
    assert "cookie env" in output.lower()
    assert misplaced_cookie not in output
    assert "SECRET_COOKIE_VALUE" not in output
    assert "SESSDATA" not in output
    assert "token" not in output.lower()
    assert "payload" not in output.lower()


def test_smoke_run_cli_reports_project_errors_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fake_runner(
        target: SmokeTarget,
        *,
        auth_mode: AuthMode,
        roi: ROIConfig,
        sample_count: int,
        target_p95_ms: float,
        max_wait_seconds: float,
        cookie: str | None,
        enable_pyzbar_fallback: bool,
        enable_roi_fallback: bool,
    ) -> SmokeReport:
        raise StreamResolveError("Bilibili room init HTTP failed")

    monkeypatch.setattr(smoke_module, "run_bilibili_decode_smoke", fake_runner)

    exit_code = main(
        [
            "smoke-run",
            "--mode",
            "real",
            "--platform",
            "bilibili",
            "--room-id",
            "12345",
            "--game-id",
            "honor_of_kings",
            "--uid",
            "10001",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "[WARN]" in output
    assert "Bilibili room init HTTP failed" in output
    assert "Traceback" not in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "payload" not in output.lower()


def test_smoke_run_cli_rejects_cookie_auth_without_cookie_env(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(
        [
            "smoke-run",
            "--mode",
            "real",
            "--platform",
            "bilibili",
            "--room-id",
            "12345",
            "--game-id",
            "honor_of_kings",
            "--uid",
            "10001",
            "--auth-mode",
            "cookie",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "[WARN]" in output
    assert "cookie env" in output.lower()
    assert "token" not in output.lower()
    assert "payload" not in output.lower()


def test_smoke_run_cli_rejects_invalid_cookie_env_name_without_echoing_value(
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw_cookie_value = "SESSDATA=SECRET_COOKIE_VALUE;bili_jct=SECRET_CSRF"

    exit_code = main(
        [
            "smoke-run",
            "--mode",
            "real",
            "--platform",
            "bilibili",
            "--room-id",
            "12345",
            "--game-id",
            "honor_of_kings",
            "--uid",
            "10001",
            "--auth-mode",
            "cookie",
            "--cookie-env",
            raw_cookie_value,
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "[WARN]" in output
    assert "cookie env name" in output.lower()
    assert raw_cookie_value not in output
    assert "SECRET_COOKIE_VALUE" not in output
    assert "SECRET_CSRF" not in output
    assert "SESSDATA" not in output
    assert "bili_jct" not in output
    assert "payload" not in output.lower()


def test_smoke_run_cli_rejects_missing_cookie_env_without_echoing_name(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    env_name = "SECRET_ACCOUNT_COOKIE_ENV"
    monkeypatch.delenv(env_name, raising=False)

    exit_code = main(
        [
            "smoke-run",
            "--mode",
            "real",
            "--platform",
            "bilibili",
            "--room-id",
            "12345",
            "--game-id",
            "honor_of_kings",
            "--uid",
            "10001",
            "--auth-mode",
            "cookie",
            "--cookie-env",
            env_name,
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "[WARN]" in output
    assert "cookie env" in output.lower()
    assert "not set or empty" in output.lower()
    assert env_name not in output
    assert "SECRET_ACCOUNT" not in output
    assert "token" not in output.lower()
    assert "payload" not in output.lower()


def test_smoke_run_cli_rejects_cookie_env_outside_cookie_auth_without_echoing_value(
    capsys: pytest.CaptureFixture[str],
) -> None:
    misplaced_cookie = "SESSDATA=SECRET_COOKIE_VALUE"

    exit_code = main(
        [
            "smoke-run",
            "--mode",
            "real",
            "--platform",
            "bilibili",
            "--room-id",
            "12345",
            "--game-id",
            "honor_of_kings",
            "--uid",
            "10001",
            "--auth-mode",
            "anonymous",
            "--cookie-env",
            misplaced_cookie,
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "[WARN]" in output
    assert "cookie env" in output.lower()
    assert "cookie auth" in output.lower()
    assert misplaced_cookie not in output
    assert "SECRET_COOKIE_VALUE" not in output
    assert "SESSDATA" not in output
    assert "token" not in output.lower()
    assert "payload" not in output.lower()


def test_smoke_run_cli_rejects_invalid_roi(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(
        [
            "smoke-run",
            "--mode",
            "real",
            "--platform",
            "bilibili",
            "--room-id",
            "12345",
            "--game-id",
            "honor_of_kings",
            "--uid",
            "10001",
            "--roi",
            "0.9,0.2,0.3,0.4",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "[WARN]" in output
    assert "ROI" in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "payload" not in output.lower()


def test_smoke_run_cli_rejects_negative_max_wait(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(
        [
            "smoke-run",
            "--mode",
            "real",
            "--platform",
            "bilibili",
            "--room-id",
            "12345",
            "--game-id",
            "honor_of_kings",
            "--uid",
            "10001",
            "--max-wait-seconds",
            "-1",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "[WARN]" in output
    assert "max wait" in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "payload" not in output.lower()


def test_smoke_run_cli_rejects_zero_max_wait(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(
        [
            "smoke-run",
            "--mode",
            "real",
            "--platform",
            "bilibili",
            "--room-id",
            "12345",
            "--game-id",
            "honor_of_kings",
            "--uid",
            "10001",
            "--max-wait-seconds",
            "0",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "[WARN]" in output
    assert "max wait" in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "payload" not in output.lower()


def test_smoke_run_cli_rejects_non_positive_target_before_runner(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def unexpected_runner(
        target: SmokeTarget,
        *,
        auth_mode: AuthMode,
        roi: ROIConfig,
        sample_count: int,
        target_p95_ms: float,
        max_wait_seconds: float,
        cookie: str | None,
    ) -> SmokeReport:
        raise AssertionError("runner should not be called")

    monkeypatch.setattr(smoke_module, "run_bilibili_decode_smoke", unexpected_runner)

    exit_code = main(
        [
            "smoke-run",
            "--mode",
            "real",
            "--platform",
            "bilibili",
            "--room-id",
            "12345",
            "--game-id",
            "honor_of_kings",
            "--uid",
            "10001",
            "--target-p95-ms",
            "0",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "[WARN]" in output
    assert "target P95" in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "payload" not in output.lower()


def test_smoke_run_cli_rejects_too_small_sample_count_before_runner(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def unexpected_runner(
        target: SmokeTarget,
        *,
        auth_mode: AuthMode,
        roi: ROIConfig,
        sample_count: int,
        target_p95_ms: float,
        max_wait_seconds: float,
        cookie: str | None,
    ) -> SmokeReport:
        raise AssertionError("runner should not be called")

    monkeypatch.setattr(smoke_module, "run_bilibili_decode_smoke", unexpected_runner)

    exit_code = main(
        [
            "smoke-run",
            "--mode",
            "real",
            "--platform",
            "bilibili",
            "--room-id",
            "12345",
            "--game-id",
            "honor_of_kings",
            "--uid",
            "10001",
            "--sample-count",
            "0",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "[WARN]" in output
    assert "sample count" in output.lower()
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "payload" not in output.lower()


def test_smoke_run_cli_rejects_non_finite_target_before_runner(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def unexpected_runner(
        target: SmokeTarget,
        *,
        auth_mode: AuthMode,
        roi: ROIConfig,
        sample_count: int,
        target_p95_ms: float,
        max_wait_seconds: float,
        cookie: str | None,
    ) -> SmokeReport:
        raise AssertionError("runner should not be called")

    monkeypatch.setattr(smoke_module, "run_bilibili_decode_smoke", unexpected_runner)

    exit_code = main(
        [
            "smoke-run",
            "--mode",
            "real",
            "--platform",
            "bilibili",
            "--room-id",
            "12345",
            "--game-id",
            "honor_of_kings",
            "--uid",
            "10001",
            "--target-p95-ms",
            "inf",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "[WARN]" in output
    assert "target P95" in output
    assert "finite" in output.lower()
    assert "inf" not in output.lower()
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "payload" not in output.lower()


def test_smoke_run_cli_rejects_non_finite_max_wait_before_runner(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def unexpected_runner(
        target: SmokeTarget,
        *,
        auth_mode: AuthMode,
        roi: ROIConfig,
        sample_count: int,
        target_p95_ms: float,
        max_wait_seconds: float,
        cookie: str | None,
    ) -> SmokeReport:
        raise AssertionError("runner should not be called")

    monkeypatch.setattr(smoke_module, "run_bilibili_decode_smoke", unexpected_runner)

    exit_code = main(
        [
            "smoke-run",
            "--mode",
            "real",
            "--platform",
            "bilibili",
            "--room-id",
            "12345",
            "--game-id",
            "honor_of_kings",
            "--uid",
            "10001",
            "--max-wait-seconds",
            "nan",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "[WARN]" in output
    assert "max wait" in output
    assert "finite" in output.lower()
    assert "nan" not in output.lower()
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "payload" not in output.lower()


def test_smoke_run_cli_outputs_synthetic_report_without_sensitive_text(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(
        [
            "smoke-run",
            "--mode",
            "synthetic",
            "--platform",
            "synthetic",
            "--room-id",
            "local",
            "--game-id",
            "honor_of_kings",
            "--uid",
            "10001",
            "--sample-count",
            "50",
            "--target-p95-ms",
            "800",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "sample_count=50" in output
    assert "P95" in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "payload" not in output.lower()


def test_smoke_run_cli_rejects_cookie_env_in_synthetic_mode_without_echoing_value(
    capsys: pytest.CaptureFixture[str],
) -> None:
    misplaced_cookie = "SESSDATA=SECRET_COOKIE_VALUE"

    exit_code = main(
        [
            "smoke-run",
            "--mode",
            "synthetic",
            "--platform",
            "synthetic",
            "--room-id",
            "local",
            "--game-id",
            "honor_of_kings",
            "--uid",
            "10001",
            "--sample-count",
            "50",
            "--cookie-env",
            misplaced_cookie,
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "[WARN]" in output
    assert "cookie env" in output.lower()
    assert misplaced_cookie not in output
    assert "SECRET_COOKIE_VALUE" not in output
    assert "SESSDATA" not in output
    assert "token" not in output.lower()
    assert "payload" not in output.lower()


def test_smoke_run_cli_rejects_non_synthetic_platform_in_synthetic_mode_without_echoing_value(
    capsys: pytest.CaptureFixture[str],
) -> None:
    sensitive_platform = "SECRET_PLATFORM_TOKEN"

    exit_code = main(
        [
            "smoke-run",
            "--mode",
            "synthetic",
            "--platform",
            sensitive_platform,
            "--room-id",
            "local",
            "--game-id",
            "honor_of_kings",
            "--uid",
            "10001",
            "--sample-count",
            "50",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "[WARN]" in output
    assert "synthetic platform" in output.lower()
    assert sensitive_platform not in output
    assert "SECRET_PLATFORM" not in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "payload" not in output.lower()


def test_smoke_run_cli_rejects_blank_uid(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(
        [
            "smoke-run",
            "--mode",
            "synthetic",
            "--platform",
            "synthetic",
            "--room-id",
            "local",
            "--game-id",
            "honor_of_kings",
            "--uid",
            "   ",
            "--sample-count",
            "50",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "[WARN]" in output
    assert "uid" in output.lower()
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "payload" not in output.lower()
