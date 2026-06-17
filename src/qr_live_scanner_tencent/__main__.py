from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import time
from pathlib import Path
from typing import Protocol

import httpx

import qr_live_scanner_tencent.smoke as smoke_module
from qr_live_scanner_tencent.accounts import (
    KeyringAccountStore,
    LocalDeviceIdStore,
    TencentAccountQRLoginError,
    TencentAccountQRLoginService,
    TencentAccountQRLoginState,
    TencentAccountQRLoginStatus,
    TencentAccountQRTicket,
    TencentSession,
    load_tencent_account_qr_login_config,
)
from qr_live_scanner_tencent.interfaces import (
    DEFAULT_AGGRESSIVE_ROI,
    AccountStoreError,
    AuthMode,
    GameID,
    QRLiveScannerError,
    ROIConfig,
    TencentLoginProvider,
)
from qr_live_scanner_tencent.security import (
    build_tencent_protocol_sample_from_har,
    redact_har,
    render_tencent_account_qr_config_skeleton,
    render_tencent_protocol_note,
)
from qr_live_scanner_tencent.security.protocol_sample import ALLOWED_TENCENT_PROTOCOL_SAMPLE_FLOWS
from qr_live_scanner_tencent.smoke import (
    DEFAULT_DECODE_SMOKE_MAX_WAIT_SECONDS,
    DEFAULT_TARGET_P95_MS,
    MIN_SMOKE_SAMPLES,
    SmokeTarget,
    build_smoke_report,
    parse_latency_csv,
    run_synthetic_smoke,
)

ENV_VAR_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class TencentAccountQRLoginServiceProtocol(Protocol):
    async def fetch_qr(self) -> TencentAccountQRTicket:
        """Create a QR ticket for rendering."""

    async def query_qr(self, ticket: TencentAccountQRTicket) -> TencentAccountQRLoginStatus:
        """Poll QR login status."""

    def write_qr_png(self, ticket: TencentAccountQRTicket, output_path: Path) -> None:
        """Render the QR payload into a local PNG."""

    async def aclose(self) -> None:
        """Close runtime resources."""


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "smoke-report":
        return _run_smoke_report(args)
    if args.command == "smoke-run":
        return _run_smoke_run(args)
    if args.command == "decode-probe":
        return _run_decode_probe(args)
    if args.command == "redact-har":
        return _run_redact_har(args)
    if args.command == "tencent-protocol-sample":
        return _run_tencent_protocol_sample(args)
    if args.command == "tencent-protocol-note":
        return _run_tencent_protocol_note(args)
    if args.command == "tencent-protocol-config-skeleton":
        return _run_tencent_protocol_config_skeleton(args)
    if args.command == "gui":
        return _run_gui(args)
    if args.command == "gui-snapshot":
        return _run_gui_snapshot(args)
    if args.command == "tencent-login":
        return _run_tencent_login(args)
    if args.command == "tencent-status":
        return _run_tencent_status(args)
    parser.print_help()
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="qr-live-scanner-tencent")
    subparsers = parser.add_subparsers(dest="command")

    smoke_parser = subparsers.add_parser("smoke-report")
    smoke_parser.add_argument("--platform", required=True, choices=["bilibili", "douyin"])
    smoke_parser.add_argument("--room-id", required=True)
    smoke_parser.add_argument("--game-id", required=True, choices=[game.value for game in GameID])
    smoke_parser.add_argument("--uid", required=True)
    smoke_parser.add_argument("--latencies-ms", required=True)
    smoke_parser.add_argument("--target-p95-ms", type=float, default=DEFAULT_TARGET_P95_MS)

    run_parser = subparsers.add_parser("smoke-run")
    run_parser.add_argument("--mode", required=True, choices=["synthetic", "real"])
    run_parser.add_argument("--platform", required=True)
    run_parser.add_argument("--room-id", required=True)
    run_parser.add_argument("--game-id", required=True, choices=[game.value for game in GameID])
    run_parser.add_argument("--uid", required=True)
    run_parser.add_argument("--sample-count", type=int, default=MIN_SMOKE_SAMPLES)
    run_parser.add_argument("--target-p95-ms", type=float, default=DEFAULT_TARGET_P95_MS)
    run_parser.add_argument(
        "--auth-mode",
        choices=[mode.value for mode in AuthMode],
        default=AuthMode.AUTO.value,
    )
    run_parser.add_argument("--roi")
    run_parser.add_argument(
        "--max-wait-seconds",
        type=float,
        default=DEFAULT_DECODE_SMOKE_MAX_WAIT_SECONDS,
    )
    run_parser.add_argument("--cookie-env")
    run_parser.add_argument("--enable-pyzbar-fallback", action="store_true")
    run_parser.add_argument("--disable-roi-fallback", action="store_true")
    run_parser.add_argument("--browser-resolver", action="store_true")
    run_parser.add_argument("--browser-user-data-dir", default="profiles/douyin")
    run_parser.add_argument("--chrome-executable-path")

    probe_parser = subparsers.add_parser("decode-probe")
    probe_parser.add_argument("--platform", required=True)
    probe_parser.add_argument("--room-id", required=True)
    probe_parser.add_argument("--game-id", required=True, choices=[game.value for game in GameID])
    probe_parser.add_argument("--uid", required=True)
    probe_parser.add_argument("--sample-count", type=int, default=1)
    probe_parser.add_argument(
        "--auth-mode",
        choices=[mode.value for mode in AuthMode],
        default=AuthMode.AUTO.value,
    )
    probe_parser.add_argument("--roi")
    probe_parser.add_argument(
        "--max-wait-seconds",
        type=float,
        default=DEFAULT_DECODE_SMOKE_MAX_WAIT_SECONDS,
    )
    probe_parser.add_argument("--cookie-env")
    probe_parser.add_argument("--enable-pyzbar-fallback", action="store_true")
    probe_parser.add_argument("--disable-roi-fallback", action="store_true")
    probe_parser.add_argument("--browser-resolver", action="store_true")
    probe_parser.add_argument("--browser-user-data-dir", default="profiles/douyin")
    probe_parser.add_argument("--chrome-executable-path")

    gui_parser = subparsers.add_parser("gui")
    gui_parser.add_argument("--dry-run", action="store_true")

    gui_snapshot_parser = subparsers.add_parser("gui-snapshot")
    gui_snapshot_parser.add_argument("--output-dir", default="work/gui-snapshots")
    gui_snapshot_parser.add_argument("--mock-uid")
    gui_snapshot_parser.add_argument(
        "--provider",
        choices=[provider.value for provider in TencentLoginProvider],
        default=TencentLoginProvider.QQ.value,
    )

    tencent_login_parser = subparsers.add_parser("tencent-login")
    tencent_login_parser.add_argument(
        "--provider",
        choices=[provider.value for provider in TencentLoginProvider],
        default=TencentLoginProvider.QQ.value,
    )
    tencent_login_parser.add_argument("--dry-run", action="store_true")
    tencent_login_parser.add_argument("--mock-confirm", action="store_true")
    tencent_login_parser.add_argument("--mock-uid")
    tencent_login_parser.add_argument("--qr-output", default="work/tencent-login-qr.png")
    tencent_login_parser.add_argument("--protocol-config")
    tencent_login_parser.add_argument("--poll-interval-seconds", type=float, default=2.0)
    tencent_login_parser.add_argument("--timeout-seconds", type=float, default=60.0)

    tencent_status_parser = subparsers.add_parser("tencent-status")
    tencent_status_parser.add_argument(
        "--provider",
        choices=[provider.value for provider in TencentLoginProvider],
        default=TencentLoginProvider.QQ.value,
    )
    tencent_status_parser.add_argument("--uid", required=True)

    redact_parser = subparsers.add_parser("redact-har")
    redact_parser.add_argument("--input", required=True)
    redact_parser.add_argument("--output", required=True)

    protocol_sample_parser = subparsers.add_parser("tencent-protocol-sample")
    protocol_sample_parser.add_argument("--input", required=True)
    protocol_sample_parser.add_argument("--output", required=True)
    protocol_sample_parser.add_argument(
        "--provider",
        choices=[provider.value for provider in TencentLoginProvider],
        default=TencentLoginProvider.QQ.value,
    )
    protocol_sample_parser.add_argument(
        "--flow",
        choices=list(ALLOWED_TENCENT_PROTOCOL_SAMPLE_FLOWS),
        default="account-login",
    )

    protocol_note_parser = subparsers.add_parser("tencent-protocol-note")
    protocol_note_parser.add_argument("--input", required=True)
    protocol_note_parser.add_argument("--output", required=True)

    protocol_config_parser = subparsers.add_parser("tencent-protocol-config-skeleton")
    protocol_config_parser.add_argument("--input", required=True)
    protocol_config_parser.add_argument("--output", required=True)
    return parser


def _run_smoke_report(args: argparse.Namespace) -> int:
    try:
        target = SmokeTarget(
            platform=str(args.platform),
            room_id=_required_text(args.room_id, "room id"),
            game_id=GameID(str(args.game_id)),
            uid=_required_text(args.uid, "uid"),
        )
        report = build_smoke_report(
            target,
            parse_latency_csv(str(args.latencies_ms)),
            target_p95_ms=float(args.target_p95_ms),
        )
    except (ValueError, QRLiveScannerError) as exc:
        print(f"[WARN] {exc}")
        return 2
    print(report.to_text())
    return 0 if report.passed else 1


def _run_smoke_run(args: argparse.Namespace) -> int:
    mode = str(args.mode)
    platform = str(args.platform)
    if mode == "real":
        if platform == "douyin" and not bool(args.browser_resolver):
            print("[WARN] Douyin real smoke requires a signed enter URL factory; not configured")
            return 2
        if platform != "bilibili" and platform != "douyin":
            print("[WARN] smoke-run real mode currently supports only bilibili and douyin")
            return 2
    try:
        target = SmokeTarget(
            platform=platform,
            room_id=_required_text(args.room_id, "room id"),
            game_id=GameID(str(args.game_id)),
            uid=_required_text(args.uid, "uid"),
        )
        target_p95_ms = _validate_target_p95(float(args.target_p95_ms))
        sample_count = _validate_sample_count(int(args.sample_count))
        if mode == "synthetic":
            if platform != "synthetic":
                msg = "synthetic platform must be synthetic"
                raise ValueError(msg)
            if str(args.cookie_env or "").strip():
                msg = "cookie env is only accepted in real cookie auth smoke mode"
                raise ValueError(msg)
            report = asyncio.run(
                run_synthetic_smoke(
                    target,
                    sample_count=sample_count,
                    target_p95_ms=target_p95_ms,
                )
            )
        else:
            auth_mode = AuthMode(str(args.auth_mode))
            if platform == "douyin":
                report = asyncio.run(
                    smoke_module.run_douyin_browser_decode_smoke(
                        target,
                        auth_mode=auth_mode,
                        roi=_optional_roi(args.roi, default=DEFAULT_AGGRESSIVE_ROI),
                        sample_count=sample_count,
                        target_p95_ms=target_p95_ms,
                        max_wait_seconds=_validate_max_wait(float(args.max_wait_seconds)),
                        user_data_dir=_required_text(
                            args.browser_user_data_dir, "browser user data dir"
                        ),
                        chrome_executable_path=_optional_text(args.chrome_executable_path),
                        enable_pyzbar_fallback=bool(args.enable_pyzbar_fallback),
                        enable_roi_fallback=not bool(args.disable_roi_fallback),
                    )
                )
            else:
                report = asyncio.run(
                    smoke_module.run_bilibili_decode_smoke(
                        target,
                        auth_mode=auth_mode,
                        roi=_optional_roi(args.roi, default=DEFAULT_AGGRESSIVE_ROI),
                        sample_count=sample_count,
                        target_p95_ms=target_p95_ms,
                        max_wait_seconds=_validate_max_wait(float(args.max_wait_seconds)),
                        cookie=_resolve_cookie(auth_mode, args.cookie_env),
                        enable_pyzbar_fallback=bool(args.enable_pyzbar_fallback),
                        enable_roi_fallback=not bool(args.disable_roi_fallback),
                    )
                )
    except (ValueError, QRLiveScannerError) as exc:
        print(f"[WARN] {exc}")
        return 2
    print(report.to_text())
    return 0 if report.passed else 1


def _run_decode_probe(args: argparse.Namespace) -> int:
    platform = str(args.platform)
    if platform == "douyin" and not bool(args.browser_resolver):
        print("[WARN] Douyin decode probe requires a signed enter URL factory; not configured")
        return 2
    if platform != "bilibili" and platform != "douyin":
        print("[WARN] decode-probe currently supports only bilibili and douyin")
        return 2
    try:
        target = SmokeTarget(
            platform=platform,
            room_id=_required_text(args.room_id, "room id"),
            game_id=GameID(str(args.game_id)),
            uid=_required_text(args.uid, "uid"),
        )
        auth_mode = AuthMode(str(args.auth_mode))
        if platform == "douyin":
            result = asyncio.run(
                smoke_module.run_douyin_browser_decode_probe(
                    target,
                    auth_mode=auth_mode,
                    roi=_optional_roi(args.roi, default=DEFAULT_AGGRESSIVE_ROI),
                    sample_count=_validate_probe_sample_count(int(args.sample_count)),
                    max_wait_seconds=_validate_probe_max_wait(float(args.max_wait_seconds)),
                    user_data_dir=_required_text(
                        args.browser_user_data_dir, "browser user data dir"
                    ),
                    chrome_executable_path=_optional_text(args.chrome_executable_path),
                    enable_pyzbar_fallback=bool(args.enable_pyzbar_fallback),
                    enable_roi_fallback=not bool(args.disable_roi_fallback),
                )
            )
        else:
            result = asyncio.run(
                smoke_module.run_bilibili_decode_probe(
                    target,
                    auth_mode=auth_mode,
                    roi=_optional_roi(args.roi, default=DEFAULT_AGGRESSIVE_ROI),
                    sample_count=_validate_probe_sample_count(int(args.sample_count)),
                    max_wait_seconds=_validate_probe_max_wait(float(args.max_wait_seconds)),
                    cookie=_resolve_cookie(auth_mode, args.cookie_env),
                    enable_pyzbar_fallback=bool(args.enable_pyzbar_fallback),
                    enable_roi_fallback=not bool(args.disable_roi_fallback),
                )
            )
    except (ValueError, QRLiveScannerError) as exc:
        print(f"[WARN] {exc}")
        return 2
    print(result.to_text())
    return 0


def _parse_roi(value: str) -> ROIConfig:
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 4:
        msg = "ROI must use x,y,width,height normalized coordinates"
        raise ValueError(msg)
    try:
        x, y, width, height = (float(part) for part in parts)
    except ValueError as exc:
        msg = "ROI values must be numeric"
        raise ValueError(msg) from exc
    return ROIConfig(x=x, y=y, width=width, height=height)


def _optional_roi(value: object, *, default: ROIConfig | None = None) -> ROIConfig | None:
    text = str(value or "").strip()
    if not text:
        return default
    return _parse_roi(text)


def _required_text(value: object, label: str) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        msg = f"{label} is required"
        raise ValueError(msg)
    return text


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _validate_max_wait(value: float) -> float:
    if not math.isfinite(value) or value <= 0:
        msg = "decode smoke max wait seconds must be finite and positive"
        raise ValueError(msg)
    return value


def _validate_target_p95(value: float) -> float:
    if not math.isfinite(value) or value <= 0:
        msg = "target P95 must be finite and positive"
        raise ValueError(msg)
    return value


def _validate_sample_count(value: int) -> int:
    if value < MIN_SMOKE_SAMPLES:
        msg = f"sample count must be at least {MIN_SMOKE_SAMPLES}"
        raise ValueError(msg)
    return value


def _validate_probe_sample_count(value: int) -> int:
    if value <= 0:
        msg = "decode probe sample count must be positive"
        raise ValueError(msg)
    return value


def _validate_probe_max_wait(value: float) -> float:
    if not math.isfinite(value) or value <= 0:
        msg = "decode probe max wait seconds must be finite and positive"
        raise ValueError(msg)
    return value


def _resolve_cookie(auth_mode: AuthMode, cookie_env: object) -> str | None:
    env_name = str(cookie_env or "").strip()
    if auth_mode is not AuthMode.COOKIE:
        if env_name:
            msg = "cookie env is only accepted with cookie auth mode"
            raise ValueError(msg)
        return None
    if not env_name:
        msg = "cookie env name is required when auth mode is cookie"
        raise ValueError(msg)
    if not ENV_VAR_NAME_PATTERN.fullmatch(env_name):
        msg = "cookie env name must be a valid environment variable name"
        raise ValueError(msg)
    cookie = os.environ.get(env_name, "").strip()
    if not cookie:
        msg = "cookie env is not set or empty"
        raise ValueError(msg)
    return cookie


def _run_redact_har(args: argparse.Namespace) -> int:
    input_path = Path(str(args.input))
    output_path = Path(str(args.output))
    try:
        if input_path.resolve() == output_path.resolve():
            msg = "HAR input and output paths must be different"
            raise ValueError(msg)
        with input_path.open("r", encoding="utf-8") as file:
            har = json.load(file)
        if not isinstance(har, dict):
            msg = "HAR root must be a JSON object"
            raise ValueError(msg)
        _validate_har_shape(har)
        redacted = redact_har(har)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as file:
            json.dump(redacted, file, ensure_ascii=False, indent=2)
            file.write("\n")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[WARN] HAR redaction failed: {exc}")
        return 2
    print(f"HAR redacted: {output_path}")
    return 0


def _run_tencent_protocol_sample(args: argparse.Namespace) -> int:
    input_path = Path(str(args.input))
    output_path = Path(str(args.output))
    try:
        if input_path.resolve() == output_path.resolve():
            msg = "protocol sample input and output paths must be different"
            raise ValueError(msg)
        with input_path.open("r", encoding="utf-8-sig") as file:
            har = json.load(file)
        if not isinstance(har, dict):
            msg = "HAR root must be a JSON object"
            raise ValueError(msg)
        _validate_har_shape(har)
        sample = build_tencent_protocol_sample_from_har(
            har,
            provider=TencentLoginProvider(str(args.provider)),
            flow=str(args.flow),
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as file:
            json.dump(sample, file, ensure_ascii=False, indent=2)
            file.write("\n")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[WARN] Tencent protocol sample import failed: {exc}")
        return 2
    print(f"Tencent protocol sample written: {output_path}")
    return 0


def _run_tencent_protocol_note(args: argparse.Namespace) -> int:
    input_path = Path(str(args.input))
    output_path = Path(str(args.output))
    try:
        if input_path.resolve() == output_path.resolve():
            msg = "protocol note input and output paths must be different"
            raise ValueError(msg)
        with input_path.open("r", encoding="utf-8-sig") as file:
            sample = json.load(file)
        if not isinstance(sample, dict):
            msg = "protocol sample root must be a JSON object"
            raise ValueError(msg)
        note = render_tencent_protocol_note(sample)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(note, encoding="utf-8")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[WARN] Tencent protocol note rendering failed: {exc}")
        return 2
    print(f"Tencent protocol note written: {output_path}")
    return 0


def _run_tencent_protocol_config_skeleton(args: argparse.Namespace) -> int:
    input_path = Path(str(args.input))
    output_path = Path(str(args.output))
    try:
        if input_path.resolve() == output_path.resolve():
            msg = "protocol config skeleton input and output paths must be different"
            raise ValueError(msg)
        with input_path.open("r", encoding="utf-8-sig") as file:
            sample = json.load(file)
        if not isinstance(sample, dict):
            msg = "protocol sample root must be a JSON object"
            raise ValueError(msg)
        skeleton = render_tencent_account_qr_config_skeleton(sample)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(skeleton, encoding="utf-8")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[WARN] Tencent protocol config skeleton rendering failed: {exc}")
        return 2
    print(f"Tencent protocol config skeleton written: {output_path}")
    return 0


def _validate_har_shape(har: dict[str, object]) -> None:
    log = har.get("log")
    if not isinstance(log, dict):
        msg = "HAR log object is required"
        raise ValueError(msg)
    entries = log.get("entries")
    if not isinstance(entries, list):
        msg = "HAR log.entries list is required"
        raise ValueError(msg)
    for entry in entries:
        if not isinstance(entry, dict):
            msg = "HAR entries must be objects"
            raise ValueError(msg)
        request = entry.get("request")
        if not isinstance(request, dict) or not isinstance(request.get("url"), str):
            msg = "HAR entries must include request.url"
            raise ValueError(msg)


def _run_gui(args: argparse.Namespace) -> int:
    if bool(args.dry_run):
        print("GUI entrypoint ready")
        return 0

    from PySide6.QtWidgets import QApplication

    from qr_live_scanner_tencent.gui import MainWindow
    from qr_live_scanner_tencent.gui.state import DEFAULT_GUI_STATE_PATH

    app = QApplication.instance() or QApplication([])
    window = MainWindow(state_path=DEFAULT_GUI_STATE_PATH)
    window.show()
    return int(app.exec())


def _run_gui_snapshot(args: argparse.Namespace) -> int:
    from qr_live_scanner_tencent.gui.snapshot import write_gui_snapshots

    try:
        paths = write_gui_snapshots(
            Path(str(args.output_dir)),
            provider=TencentLoginProvider(str(args.provider)),
            mock_uid=_optional_text(args.mock_uid) or "",
        )
    except RuntimeError as exc:
        print(f"[WARN] {exc}")
        return 2
    for path in paths:
        print(f"GUI snapshot written: {path}")
    if _optional_text(args.mock_uid):
        print("GUI mock account snapshot rendered")
    return 0


def _run_tencent_login(args: argparse.Namespace) -> int:
    try:
        provider = TencentLoginProvider(str(args.provider))
        qr_output_path = Path(_required_text(args.qr_output, "QR output path"))
        timeout_seconds = _validate_tencent_login_timeout(float(args.timeout_seconds))
        poll_interval_seconds = _validate_tencent_login_poll_interval(
            float(args.poll_interval_seconds)
        )
        if bool(args.mock_confirm):
            _run_tencent_login_mock_confirm(
                provider=provider,
                qr_output_path=qr_output_path,
                mock_uid=_required_text(args.mock_uid, "mock uid is required"),
            )
            return 0

        if bool(args.dry_run):
            service = TencentAccountQRLoginService.dry_run(
                provider=provider,
                device_id_store=LocalDeviceIdStore.default(),
            )
            try:
                ticket = service.dry_run_ticket()
                service.write_qr_png(ticket, qr_output_path)
            finally:
                asyncio.run(service.aclose())
            print(f"Tencent account QR dry-run image written: {qr_output_path}")
            print("Tencent account login dry-run ready")
            return 0

        service = _new_tencent_account_qr_login_service(
            provider,
            protocol_config_path=_optional_text(args.protocol_config),
        )
        session = asyncio.run(
            _capture_tencent_session_from_qr(
                service,
                qr_output_path=qr_output_path,
                timeout_seconds=timeout_seconds,
                poll_interval_seconds=poll_interval_seconds,
            )
        )
        if session.provider is not provider:
            msg = "Tencent account provider mismatch"
            raise TencentAccountQRLoginError(msg)
        KeyringAccountStore().save_tencent_session(session, authorized=True)
    except (ValueError, QRLiveScannerError, TencentAccountQRLoginError) as exc:
        print(f"[WARN] Tencent account QR login failed: {exc}")
        return 2
    print("Tencent account session saved")
    return 0


def _run_tencent_login_mock_confirm(
    *,
    provider: TencentLoginProvider,
    qr_output_path: Path,
    mock_uid: str,
) -> None:
    service = TencentAccountQRLoginService.dry_run(
        provider=provider,
        device_id_store=LocalDeviceIdStore.default(),
    )
    try:
        ticket = service.dry_run_ticket()
        service.write_qr_png(ticket, qr_output_path)
    finally:
        asyncio.run(service.aclose())

    KeyringAccountStore().save_tencent_session(
        TencentSession(
            uid=mock_uid,
            provider=provider,
            credentials={"mock_session": "local-mock-only"},
        ),
        authorized=True,
    )
    print(f"Tencent account QR mock image written: {qr_output_path}")
    print("mock Tencent account session saved")


def _run_tencent_status(args: argparse.Namespace) -> int:
    try:
        provider = TencentLoginProvider(str(args.provider))
        uid = _required_text(args.uid, "uid")
        store = KeyringAccountStore()
        session = store.get_tencent_session(uid, provider)
        if session is None:
            print("Tencent account session status: missing")
            return 1
        if not store.is_tencent_authorized(uid, provider):
            print("Tencent account session status: saved but not authorized")
            return 1
    except AccountStoreError:
        print("[WARN] Tencent account status failed: credential storage unavailable")
        return 2
    except ValueError as exc:
        print(f"[WARN] Tencent account status failed: {exc}")
        return 2
    print("Tencent account session status: saved and authorized")
    return 0


def _new_tencent_account_qr_login_service(
    provider: TencentLoginProvider,
    *,
    protocol_config_path: str | Path | None = None,
) -> TencentAccountQRLoginService:
    if protocol_config_path is None:
        config = TencentAccountQRLoginService.default_configs()[provider]
    else:
        config = load_tencent_account_qr_login_config(protocol_config_path, provider)
    return TencentAccountQRLoginService(
        client=httpx.AsyncClient(timeout=10.0),
        device_id_store=LocalDeviceIdStore.default(),
        config=config,
    )


def _validate_tencent_login_timeout(value: float) -> float:
    if not math.isfinite(value) or value <= 0:
        msg = "timeout seconds must be finite and positive"
        raise ValueError(msg)
    return value


def _validate_tencent_login_poll_interval(value: float) -> float:
    if not math.isfinite(value) or value <= 0:
        msg = "poll interval seconds must be finite and positive"
        raise ValueError(msg)
    return value


async def _capture_tencent_session_from_qr(
    service: TencentAccountQRLoginServiceProtocol,
    *,
    qr_output_path: Path,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> TencentSession:
    try:
        ticket = await service.fetch_qr()
        service.write_qr_png(ticket, qr_output_path)
        print(f"Tencent account QR image written: {qr_output_path}")
        deadline = time.monotonic() + timeout_seconds
        scanned_reported = False
        while time.monotonic() < deadline:
            status = await service.query_qr(ticket)
            if status.state is TencentAccountQRLoginState.SCANNED and not scanned_reported:
                scanned_reported = True
                print("Tencent account QR scanned; waiting for mobile confirmation")
            elif status.state is TencentAccountQRLoginState.CONFIRMED:
                if status.session is None:
                    msg = "confirmed Tencent account session is missing"
                    raise TencentAccountQRLoginError(msg)
                return status.session
            elif status.state is TencentAccountQRLoginState.EXPIRED:
                msg = "Tencent account QR login expired"
                raise TencentAccountQRLoginError(msg)
            elif status.state is TencentAccountQRLoginState.FAILED:
                msg = "Tencent account QR login failed"
                raise TencentAccountQRLoginError(msg)

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(poll_interval_seconds, remaining))

        msg = "Tencent account QR login timed out"
        raise TencentAccountQRLoginError(msg)
    finally:
        try:
            await service.aclose()
        except Exception:
            print("[WARN] Tencent account QR service close failed")
        finally:
            try:
                _remove_tencent_qr_png(qr_output_path)
            except TencentAccountQRLoginError as exc:
                print(f"[WARN] Tencent account QR cleanup failed: {exc}")


def _remove_tencent_qr_png(output_path: Path) -> None:
    try:
        output_path.unlink(missing_ok=True)
    except OSError as exc:
        msg = "Tencent account QR cleanup failed"
        raise TencentAccountQRLoginError(msg) from exc


if __name__ == "__main__":
    raise SystemExit(main())
