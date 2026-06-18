from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import socket
import subprocess
import time
import tomllib
from contextlib import suppress
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

import httpx

import qr_live_scanner_tencent.smoke as smoke_module
from qr_live_scanner_tencent.accounts import (
    KeyringAccountStore,
    LocalDeviceIdStore,
    TencentAccountQRLoginConfig,
    TencentAccountQRLoginError,
    TencentAccountQRLoginProtocolMode,
    TencentAccountQRLoginService,
    TencentAccountQRLoginState,
    TencentAccountQRLoginStatus,
    TencentAccountQRTicket,
    TencentSession,
    load_tencent_account_qr_login_config,
)
from qr_live_scanner_tencent.accounts.tencent_qr_login import (
    TENCENT_QQ_APP_SECRET_ENV,
    TENCENT_WECHAT_APP_SECRET_ENV,
)
from qr_live_scanner_tencent.interfaces import (
    DEFAULT_AGGRESSIVE_ROI,
    AccountStore,
    AccountStoreError,
    AuthMode,
    GameID,
    QRLiveScannerError,
    ROIConfig,
    TencentAccountIndexRepairResult,
    TencentLoginProvider,
)
from qr_live_scanner_tencent.security import (
    build_tencent_protocol_sample_from_har,
    check_tencent_protocol_artifacts,
    check_tencent_protocol_readiness,
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
TENCENT_PROTOCOL_EXAMPLE_PACK_DIR = Path("examples/tencent-protocol-research-pack")
TENCENT_PROTOCOL_EXAMPLE_SAMPLE = "qq-account-login.sample.json"
TENCENT_PROTOCOL_EXAMPLE_CONFIG = "qq-account-login.toml"
TENCENT_PROTOCOL_EXAMPLE_NOTE = "qq-account-login.note.md"


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
    if args.command == "tencent-protocol-config-check":
        return _run_tencent_protocol_config_check(args)
    if args.command == "tencent-protocol-example-check":
        return _run_tencent_protocol_example_check(args)
    if args.command == "tencent-protocol-artifact-check":
        return _run_tencent_protocol_artifact_check(args)
    if args.command == "tencent-protocol-readiness":
        return _run_tencent_protocol_readiness(args)
    if args.command == "tencent-protocol-guide":
        return _run_tencent_protocol_guide(args)
    if args.command == "tencent-protocol-next-steps":
        return _run_tencent_protocol_next_steps(args)
    if args.command == "tencent-protocol-preflight":
        return _run_tencent_protocol_preflight(args)
    if args.command == "gui":
        return _run_gui(args)
    if args.command == "gui-snapshot":
        return _run_gui_snapshot(args)
    if args.command == "tencent-login":
        return _run_tencent_login(args)
    if args.command == "tencent-login-preflight":
        return _run_tencent_login_preflight(args)
    if args.command == "tencent-list":
        return _run_tencent_list(args)
    if args.command == "tencent-repair-index":
        return _run_tencent_repair_index(args)
    if args.command == "tencent-status":
        return _run_tencent_status(args)
    if args.command == "tencent-delete":
        return _run_tencent_delete(args)
    if args.command == "tencent-account-smoke":
        return _run_tencent_account_smoke(args)
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

    tencent_login_preflight_parser = subparsers.add_parser("tencent-login-preflight")
    tencent_login_preflight_parser.add_argument(
        "--provider",
        choices=[provider.value for provider in TencentLoginProvider],
        default=TencentLoginProvider.QQ.value,
    )
    tencent_login_preflight_parser.add_argument("--protocol-config", required=True)

    tencent_list_parser = subparsers.add_parser("tencent-list")
    tencent_list_parser.add_argument(
        "--provider",
        choices=[provider.value for provider in TencentLoginProvider],
        default=TencentLoginProvider.QQ.value,
    )

    tencent_repair_index_parser = subparsers.add_parser("tencent-repair-index")
    tencent_repair_index_parser.add_argument(
        "--provider",
        choices=[provider.value for provider in TencentLoginProvider],
        default=TencentLoginProvider.QQ.value,
    )

    tencent_status_parser = subparsers.add_parser("tencent-status")
    tencent_status_parser.add_argument(
        "--provider",
        choices=[provider.value for provider in TencentLoginProvider],
        default=TencentLoginProvider.QQ.value,
    )
    tencent_status_parser.add_argument("--uid", required=True)

    tencent_delete_parser = subparsers.add_parser("tencent-delete")
    tencent_delete_parser.add_argument(
        "--provider",
        choices=[provider.value for provider in TencentLoginProvider],
        default=TencentLoginProvider.QQ.value,
    )
    tencent_delete_parser.add_argument("--uid", required=True)

    tencent_account_smoke_parser = subparsers.add_parser("tencent-account-smoke")
    tencent_account_smoke_parser.add_argument(
        "--provider",
        choices=[provider.value for provider in TencentLoginProvider],
        default=TencentLoginProvider.QQ.value,
    )
    tencent_account_smoke_parser.add_argument("--uid", required=True)
    tencent_account_smoke_parser.add_argument("--cleanup", action="store_true")

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

    protocol_config_check_parser = subparsers.add_parser("tencent-protocol-config-check")
    protocol_config_check_parser.add_argument("--config", required=True)
    protocol_config_check_parser.add_argument(
        "--provider",
        choices=[provider.value for provider in TencentLoginProvider],
        default=TencentLoginProvider.QQ.value,
    )

    subparsers.add_parser("tencent-protocol-example-check")

    protocol_artifact_check_parser = subparsers.add_parser("tencent-protocol-artifact-check")
    protocol_artifact_check_parser.add_argument("--sample", required=True)
    protocol_artifact_check_parser.add_argument("--config", required=True)

    protocol_readiness_parser = subparsers.add_parser("tencent-protocol-readiness")
    protocol_readiness_parser.add_argument("--sample", required=True)
    protocol_readiness_parser.add_argument("--config", required=True)
    protocol_readiness_parser.add_argument("--note", required=True)

    protocol_guide_parser = subparsers.add_parser("tencent-protocol-guide")
    protocol_guide_parser.add_argument(
        "--provider",
        choices=[provider.value for provider in TencentLoginProvider],
        default=TencentLoginProvider.QQ.value,
    )

    protocol_next_steps_parser = subparsers.add_parser("tencent-protocol-next-steps")
    protocol_next_steps_parser.add_argument(
        "--provider",
        choices=[provider.value for provider in TencentLoginProvider],
        default=TencentLoginProvider.QQ.value,
    )

    subparsers.add_parser("tencent-protocol-preflight")
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


def _run_tencent_protocol_config_check(args: argparse.Namespace) -> int:
    config_path = Path(str(args.config))
    try:
        provider = TencentLoginProvider(str(args.provider))
    except ValueError as exc:
        print(f"[WARN] Tencent protocol config check failed: {exc}")
        return 2
    try:
        config = load_tencent_account_qr_login_config(config_path, provider)
    except (OSError, ValueError, TencentAccountQRLoginError) as exc:
        print(f"[WARN] Tencent protocol config check failed: provider={provider.value}: {exc}")
        return 2

    print("Tencent protocol config check passed")
    print(
        " ".join(
            [
                f"provider={config.provider.value}",
                f"validated_protocol={str(config.validated_protocol).lower()}",
                "endpoints=2",
                "app_id=present",
                "real_http=not-called",
            ]
        )
    )
    return 0


def _run_tencent_protocol_example_check(_args: argparse.Namespace) -> int:
    sample_path = TENCENT_PROTOCOL_EXAMPLE_PACK_DIR / TENCENT_PROTOCOL_EXAMPLE_SAMPLE
    config_path = TENCENT_PROTOCOL_EXAMPLE_PACK_DIR / TENCENT_PROTOCOL_EXAMPLE_CONFIG
    note_path = TENCENT_PROTOCOL_EXAMPLE_PACK_DIR / TENCENT_PROTOCOL_EXAMPLE_NOTE
    try:
        with sample_path.open("r", encoding="utf-8-sig") as file:
            sample = json.load(file)
        if not isinstance(sample, dict):
            msg = "protocol sample root must be a JSON object"
            raise ValueError(msg)
        with config_path.open("rb") as file:
            config = tomllib.load(file)
        note_text = note_path.read_text(encoding="utf-8-sig")
        check_tencent_protocol_artifacts(sample, config)
        readiness = check_tencent_protocol_readiness(sample, config, note_text)
    except OSError:
        print("[WARN] Tencent protocol example check failed: example files could not be read")
        return 2
    except json.JSONDecodeError:
        print("[WARN] Tencent protocol example check failed: protocol sample could not be parsed")
        return 2
    except tomllib.TOMLDecodeError:
        print("[WARN] Tencent protocol example check failed: protocol config could not be parsed")
        return 2
    except ValueError as exc:
        print(f"[WARN] Tencent protocol example check failed: {exc}")
        return 2

    if not readiness.ready:
        print("Tencent protocol example check blocked")
        print(
            " ".join(
                [
                    "artifacts=passed",
                    "readiness=blocked",
                    f"provider={readiness.provider.value}",
                    f"flow={readiness.flow}",
                    f"entries={readiness.entry_count}",
                    f"checked={readiness.checked_count}/{readiness.total_count}",
                    "real_http=disabled",
                ]
            )
        )
        return 1

    print("Tencent protocol example check passed")
    print(
        " ".join(
            [
                "artifacts=passed",
                "readiness=passed",
                f"provider={readiness.provider.value}",
                f"flow={readiness.flow}",
                f"entries={readiness.entry_count}",
                f"checked={readiness.checked_count}/{readiness.total_count}",
                "real_http=disabled",
            ]
        )
    )
    return 0


def _run_tencent_protocol_artifact_check(args: argparse.Namespace) -> int:
    sample_path = Path(str(args.sample))
    config_path = Path(str(args.config))
    try:
        with sample_path.open("r", encoding="utf-8-sig") as file:
            sample = json.load(file)
        if not isinstance(sample, dict):
            msg = "protocol sample root must be a JSON object"
            raise ValueError(msg)
        with config_path.open("rb") as file:
            config = tomllib.load(file)
        result = check_tencent_protocol_artifacts(sample, config)
    except OSError:
        print("[WARN] Tencent protocol artifact check failed: artifact files could not be read")
        return 2
    except json.JSONDecodeError:
        print("[WARN] Tencent protocol artifact check failed: protocol sample could not be parsed")
        return 2
    except tomllib.TOMLDecodeError:
        print("[WARN] Tencent protocol artifact check failed: protocol config could not be parsed")
        return 2
    except ValueError as exc:
        print(f"[WARN] Tencent protocol artifact check failed: {exc}")
        return 2

    print("Tencent protocol artifacts passed")
    print(
        " ".join(
            [
                "sample",
                "source=redacted-har",
                f"provider={result.provider.value}",
                f"flow={result.flow}",
                f"entries={result.entry_count}",
            ]
        )
    )
    print(f"config provider={result.provider.value} validated_protocol = false")
    return 0


def _run_tencent_protocol_readiness(args: argparse.Namespace) -> int:
    sample_path = Path(str(args.sample))
    config_path = Path(str(args.config))
    note_path = Path(str(args.note))
    try:
        with sample_path.open("r", encoding="utf-8-sig") as file:
            sample = json.load(file)
        if not isinstance(sample, dict):
            msg = "protocol sample root must be a JSON object"
            raise ValueError(msg)
        with config_path.open("rb") as file:
            config = tomllib.load(file)
        note_text = note_path.read_text(encoding="utf-8-sig")
        result = check_tencent_protocol_readiness(sample, config, note_text)
    except OSError:
        print("[WARN] Tencent protocol readiness failed: artifact files could not be read")
        return 2
    except json.JSONDecodeError:
        print("[WARN] Tencent protocol readiness failed: protocol sample could not be parsed")
        return 2
    except tomllib.TOMLDecodeError:
        print("[WARN] Tencent protocol readiness failed: protocol config could not be parsed")
        return 2
    except ValueError as exc:
        print(f"[WARN] Tencent protocol readiness failed: {exc}")
        return 2

    summary = " ".join(
        [
            f"provider={result.provider.value}",
            f"flow={result.flow}",
            f"entries={result.entry_count}",
            f"checked={result.checked_count}/{result.total_count}",
            "real_http=disabled",
        ]
    )
    if not result.ready:
        print("Tencent protocol readiness blocked")
        print(summary)
        print(f"unchecked={len(result.missing_items)}")
        for item in result.missing_items:
            print(f"- {item}")
        return 1

    print("Tencent protocol readiness passed")
    print(summary)
    return 0


def _run_tencent_protocol_guide(args: argparse.Namespace) -> int:
    provider = TencentLoginProvider(str(args.provider))
    print("Safe Tencent protocol capture workflow")
    print(f"provider: {provider.value}")
    print(
        "Do not share raw HAR, Cookie, Authorization, token, openid, "
        "qrsig, ticket, UID, or QR URL."
    )
    print("1. qr-live-scanner-tencent tencent-protocol-preflight")
    print("2. Export the local browser capture to captures/tencent-login.har")
    print(
        "3. qr-live-scanner-tencent redact-har "
        "--input captures/tencent-login.har "
        "--output captures/tencent-login.redacted.har"
    )
    print(
        "4. qr-live-scanner-tencent tencent-protocol-sample "
        "--input captures/tencent-login.redacted.har "
        "--output captures/tencent-login.sample.json "
        f"--provider {provider.value} --flow account-login"
    )
    print(
        "5. qr-live-scanner-tencent tencent-protocol-note "
        "--input captures/tencent-login.sample.json "
        "--output captures/tencent-login.note.md"
    )
    print(
        "6. qr-live-scanner-tencent tencent-protocol-config-skeleton "
        "--input captures/tencent-login.sample.json "
        "--output profiles/tencent-account-login.toml"
    )
    print(
        "7. qr-live-scanner-tencent tencent-protocol-artifact-check "
        "--sample captures/tencent-login.sample.json "
        "--config profiles/tencent-account-login.toml"
    )
    print(
        "8. qr-live-scanner-tencent tencent-protocol-readiness "
        "--sample captures/tencent-login.sample.json "
        "--config profiles/tencent-account-login.toml "
        "--note captures/tencent-login.note.md"
    )
    print("9. Inspect only the redacted HAR, sample JSON, note, and TOML skeleton.")
    print("10. Keep validated_protocol = false until endpoints and response rules are verified.")
    return 0


def _run_tencent_protocol_next_steps(args: argparse.Namespace) -> int:
    provider = TencentLoginProvider(str(args.provider))
    print("Tencent protocol next steps")
    print(f"provider={provider.value} flow=account-login real_http=disabled")
    for command in _tencent_protocol_next_step_commands(provider):
        print(command)
    print("Keep validated_protocol = false until endpoints and response rules are verified.")
    return 0


def _tencent_protocol_next_step_commands(provider: TencentLoginProvider) -> tuple[str, ...]:
    return (
        "qr-live-scanner-tencent tencent-protocol-example-check",
        "qr-live-scanner-tencent tencent-protocol-preflight",
        (
            "qr-live-scanner-tencent redact-har "
            "--input captures/tencent-login.har "
            "--output captures/tencent-login.redacted.har"
        ),
        (
            "qr-live-scanner-tencent tencent-protocol-sample "
            "--input captures/tencent-login.redacted.har "
            "--output captures/tencent-login.sample.json "
            f"--provider {provider.value} "
            "--flow account-login"
        ),
        (
            "qr-live-scanner-tencent tencent-protocol-note "
            "--input captures/tencent-login.sample.json "
            "--output captures/tencent-login.note.md"
        ),
        (
            "qr-live-scanner-tencent tencent-protocol-config-skeleton "
            "--input captures/tencent-login.sample.json "
            "--output profiles/tencent-account-login.toml"
        ),
        (
            "qr-live-scanner-tencent tencent-protocol-artifact-check "
            "--sample captures/tencent-login.sample.json "
            "--config profiles/tencent-account-login.toml"
        ),
        (
            "qr-live-scanner-tencent tencent-protocol-readiness "
            "--sample captures/tencent-login.sample.json "
            "--config profiles/tencent-account-login.toml "
            "--note captures/tencent-login.note.md"
        ),
    )


def _run_tencent_protocol_preflight(_args: argparse.Namespace) -> int:
    gitignore_path = Path(".gitignore")
    try:
        gitignore_text = gitignore_path.read_text(encoding="utf-8")
    except OSError:
        print("Tencent protocol preflight failed: .gitignore missing or unreadable")
        return 2

    rules = _gitignore_rules(gitignore_text)
    required_rules = ("captures/", "profiles/")
    missing = [rule for rule in required_rules if rule not in rules]
    if missing:
        print("Tencent protocol preflight failed: missing gitignore rules")
        for rule in missing:
            print(f"- {rule}")
        return 2

    sensitive_paths = ("captures/tencent-login.har", "profiles/tencent-account-login.toml")
    tracked_paths = _git_tracked_paths(sensitive_paths)
    if tracked_paths:
        print("Tencent protocol preflight failed: sensitive paths are already tracked by git")
        for path in tracked_paths:
            print(f"- {path}")
        return 2

    unignored_paths = _git_unignored_paths(sensitive_paths)
    if unignored_paths:
        print("Tencent protocol preflight failed: sensitive paths are not ignored by git")
        for path in unignored_paths:
            print(f"- {path}")
        return 2

    print("Tencent protocol preflight passed")
    print("captures/ ignored")
    print("profiles/ ignored")
    return 0


def _gitignore_rules(gitignore_text: str) -> set[str]:
    rules: set[str] = set()
    for line in gitignore_text.splitlines():
        normalized = line.strip()
        if not normalized or normalized.startswith("#") or normalized.startswith("!"):
            continue
        rules.add(normalized)
    return rules


def _git_unignored_paths(paths: tuple[str, ...]) -> list[str]:
    unignored_paths: list[str] = []
    for path in paths:
        result = subprocess.run(
            ["git", "check-ignore", "--quiet", path],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode != 0:
            unignored_paths.append(path)
    return unignored_paths


def _git_tracked_paths(paths: tuple[str, ...]) -> list[str]:
    tracked_paths: list[str] = []
    for path in paths:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", path],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            tracked_paths.append(path)
    return tracked_paths


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
            return _run_tencent_login_mock_confirm(
                provider=provider,
                qr_output_path=qr_output_path,
                mock_uid=_required_text(args.mock_uid, "mock uid is required"),
            )

        if bool(args.dry_run):
            protocol_config_path = _optional_text(args.protocol_config)
            if protocol_config_path is not None:
                load_tencent_account_qr_login_config(protocol_config_path, provider)
                print(f"Tencent protocol config checked: provider={provider.value}")
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
        store = KeyringAccountStore()
        _save_tencent_session_with_index_verification(store, session)
    except AccountStoreError:
        print("[WARN] Tencent account QR login failed: credential storage unavailable")
        return 2
    except (ValueError, QRLiveScannerError, TencentAccountQRLoginError) as exc:
        print(f"[WARN] Tencent account QR login failed: {exc}")
        return 2
    print("Tencent account session saved")
    print("Tencent account index verified")
    return 0


def _run_tencent_login_preflight(args: argparse.Namespace) -> int:
    provider = TencentLoginProvider(str(args.provider))
    try:
        config_path = Path(_required_text(args.protocol_config, "protocol config path"))
        config = load_tencent_account_qr_login_config(config_path, provider)
        secret_state = _check_tencent_login_preflight_secret(config)
        callback_state = _check_tencent_login_preflight_callback_bind(config)
    except (OSError, ValueError, QRLiveScannerError, TencentAccountQRLoginError) as exc:
        print(
            "[WARN] Tencent account login preflight failed: "
            f"provider={provider.value} {exc}"
        )
        return 2
    print("Tencent account login preflight passed")
    print(
        f"provider={provider.value} "
        f"protocol_mode={config.protocol_mode.value} "
        f"secret_env={secret_state} "
        f"callback_bind={callback_state} "
        "real_http=not-called"
    )
    return 0


def _run_tencent_login_mock_confirm(
    *,
    provider: TencentLoginProvider,
    qr_output_path: Path,
    mock_uid: str,
) -> int:
    try:
        store = KeyringAccountStore()
        if store.get_tencent_session(mock_uid, provider) is not None:
            print("[WARN] mock Tencent account session failed: session already exists")
            return 1
    except AccountStoreError:
        print("[WARN] mock Tencent account session failed: credential storage unavailable")
        return 2

    service = TencentAccountQRLoginService.dry_run(
        provider=provider,
        device_id_store=LocalDeviceIdStore.default(),
    )
    try:
        ticket = service.dry_run_ticket()
        service.write_qr_png(ticket, qr_output_path)
    finally:
        asyncio.run(service.aclose())

    try:
        _save_tencent_session_with_index_verification(
            store,
            TencentSession(
                uid=mock_uid,
                provider=provider,
                credentials={"mock_session": "local-mock-only"},
            ),
        )
    except AccountStoreError:
        with suppress(OSError):
            qr_output_path.unlink(missing_ok=True)
        print("[WARN] mock Tencent account session failed: credential storage unavailable")
        return 2
    except TencentAccountQRLoginError as exc:
        with suppress(OSError):
            qr_output_path.unlink(missing_ok=True)
        print(f"[WARN] mock Tencent account session failed: {exc}")
        return 1
    print(f"Tencent account QR mock image written: {qr_output_path}")
    print("mock Tencent account session saved")
    print("mock Tencent account index verified")
    return 0


def _run_tencent_list(args: argparse.Namespace) -> int:
    try:
        provider = TencentLoginProvider(str(args.provider))
        entries = KeyringAccountStore().list_tencent_sessions(provider)
    except AccountStoreError:
        print("[WARN] Tencent account list failed: credential storage unavailable")
        return 2
    except ValueError as exc:
        print(f"[WARN] Tencent account list failed: {exc}")
        return 2
    print(f"Tencent account sessions: {len(entries)}")
    for index, entry in enumerate(entries, start=1):
        authorized = "yes" if entry.authorized else "no"
        print(f"#{index} provider={entry.provider.value} authorized={authorized}")
    return 0


def _run_tencent_repair_index(args: argparse.Namespace) -> int:
    try:
        provider = TencentLoginProvider(str(args.provider))
        result = KeyringAccountStore().repair_tencent_index(provider)
    except AccountStoreError:
        print("[WARN] Tencent account index repair failed: credential storage unavailable")
        return 2
    except ValueError as exc:
        print(f"[WARN] Tencent account index repair failed: {exc}")
        return 2
    print("Tencent account index checked")
    print(
        " ".join(
            [
                f"provider={result.provider.value}",
                f"sessions={len(result.entries)}",
                f"rebuilt={_yes_no(result.rebuilt_index)}",
                f"stale_removed={result.removed_stale_entries}",
            ]
        )
    )
    return 0


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
        if not _tencent_account_index_contains(store, uid, provider, authorized=True):
            print("[WARN] Tencent account status failed: index verification failed")
            return 1
    except AccountStoreError:
        print("[WARN] Tencent account status failed: credential storage unavailable")
        return 2
    except ValueError as exc:
        print(f"[WARN] Tencent account status failed: {exc}")
        return 2
    print("Tencent account session status: saved and authorized")
    print("Tencent account index verified")
    return 0


def _run_tencent_delete(args: argparse.Namespace) -> int:
    try:
        provider = TencentLoginProvider(str(args.provider))
        uid = _required_text(args.uid, "uid")
        store = KeyringAccountStore()
        store.delete_tencent_session(uid, provider)
        repair_result = store.repair_tencent_index(provider)
        if _tencent_account_index_result_contains(repair_result, uid, provider):
            print("[WARN] Tencent account delete failed: index cleanup missing")
            return 1
    except AccountStoreError:
        print("[WARN] Tencent account delete failed: credential storage unavailable")
        return 2
    except ValueError as exc:
        print(f"[WARN] Tencent account delete failed: {exc}")
        return 2
    print("Tencent account session deleted")
    print("Tencent account index cleanup verified")
    return 0


def _run_tencent_account_smoke(args: argparse.Namespace) -> int:
    try:
        provider = TencentLoginProvider(str(args.provider))
        uid = _required_text(args.uid, "uid")
        store = KeyringAccountStore()
        if store.get_tencent_session(uid, provider) is not None:
            print("[WARN] Tencent account local smoke failed: session already exists")
            return 1
        store.save_tencent_session(
            TencentSession(
                uid=uid,
                provider=provider,
                credentials={"mock_session": "local-smoke-only"},
            ),
            authorized=True,
        )
        session = store.get_tencent_session(uid, provider)
        if session is None:
            print("[WARN] Tencent account local smoke failed: session missing")
            return 1
        if session.provider is not provider:
            print("[WARN] Tencent account local smoke failed: provider mismatch")
            return 1
        if not store.is_tencent_authorized(uid, provider):
            print("[WARN] Tencent account local smoke failed: authorization missing")
            return 1
        if not _tencent_account_index_contains(store, uid, provider, authorized=True):
            print("[WARN] Tencent account local smoke failed: index missing")
            return 1
        print("Tencent account local smoke passed")
        print("Tencent account local index verified")
        if bool(args.cleanup):
            store.delete_tencent_session(uid, provider)
            print("Tencent account local smoke cleaned up")
            if _tencent_account_index_contains(store, uid, provider, authorized=None):
                print("[WARN] Tencent account local smoke failed: index cleanup missing")
                return 1
            print("Tencent account local index cleaned up")
    except AccountStoreError:
        print("[WARN] Tencent account local smoke failed: credential storage unavailable")
        return 2
    except ValueError as exc:
        print(f"[WARN] Tencent account local smoke failed: {exc}")
        return 2
    return 0


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _save_tencent_session_with_index_verification(
    store: AccountStore,
    session: TencentSession,
) -> None:
    store.save_tencent_session(session, authorized=True)
    if not _tencent_account_index_contains(
        store,
        session.uid,
        session.provider,
        authorized=True,
    ):
        msg = "index verification failed"
        raise TencentAccountQRLoginError(msg)


def _tencent_account_index_contains(
    store: AccountStore,
    uid: str,
    provider: TencentLoginProvider,
    *,
    authorized: bool | None,
) -> bool:
    entries = store.list_tencent_sessions(provider)
    for entry in entries:
        if entry.provider is provider and entry.uid == uid:
            return authorized is None or entry.authorized is authorized
    return False


def _tencent_account_index_result_contains(
    result: TencentAccountIndexRepairResult,
    uid: str,
    provider: TencentLoginProvider,
) -> bool:
    return any(entry.provider is provider and entry.uid == uid for entry in result.entries)


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


def _check_tencent_login_preflight_secret(config: TencentAccountQRLoginConfig) -> str:
    env_name = _tencent_login_secret_env_name(config.protocol_mode)
    if env_name is None:
        return "not-required"
    if not _optional_text(os.environ.get(env_name)):
        msg = f"secret_env missing: {env_name}"
        raise TencentAccountQRLoginError(msg)
    return "present"


def _tencent_login_secret_env_name(
    protocol_mode: TencentAccountQRLoginProtocolMode,
) -> str | None:
    if protocol_mode is TencentAccountQRLoginProtocolMode.QQ_QRCONNECT:
        return TENCENT_QQ_APP_SECRET_ENV
    if protocol_mode is TencentAccountQRLoginProtocolMode.WECHAT_QRCONNECT:
        return TENCENT_WECHAT_APP_SECRET_ENV
    return None


def _check_tencent_login_preflight_callback_bind(
    config: TencentAccountQRLoginConfig,
) -> str:
    if not config.callback_bind_url:
        return "not-required"
    parsed = urlparse(config.callback_bind_url)
    host = str(parsed.hostname or "")
    port = parsed.port
    if not host or port is None:
        msg = "callback_bind invalid"
        raise TencentAccountQRLoginError(msg)
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    probe = socket.socket(family, socket.SOCK_STREAM)
    try:
        probe.bind((host, port))
    except OSError as exc:
        msg = "callback_bind unavailable"
        raise TencentAccountQRLoginError(msg) from exc
    finally:
        probe.close()
    return "available"


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
