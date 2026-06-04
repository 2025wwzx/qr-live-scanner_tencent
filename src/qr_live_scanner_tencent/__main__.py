from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
from pathlib import Path

import qr_live_scanner_tencent.smoke as smoke_module
from qr_live_scanner_tencent.interfaces import (
    DEFAULT_AGGRESSIVE_ROI,
    AuthMode,
    GameID,
    QRLiveScannerError,
    ROIConfig,
)
from qr_live_scanner_tencent.security import redact_har
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
    if args.command == "gui":
        return _run_gui(args)
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

    redact_parser = subparsers.add_parser("redact-har")
    redact_parser.add_argument("--input", required=True)
    redact_parser.add_argument("--output", required=True)
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
    text = str(value).strip()
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


if __name__ == "__main__":
    raise SystemExit(main())
