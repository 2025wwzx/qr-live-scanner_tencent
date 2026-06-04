import time

import numpy as np
import pytest

from qr_live_scanner_tencent.accounts import FakeAccountStore
from qr_live_scanner_tencent.interfaces import (
    AccountRef,
    AuthMode,
    ConfirmResult,
    FramePacket,
    GameID,
    QRCandidate,
    ROIConfig,
    ScanResult,
    StreamFormat,
    StreamInfo,
)
from qr_live_scanner_tencent.logging import scrub_sensitive_event


def test_roi_config_uses_normalized_coordinates() -> None:
    roi = ROIConfig(x=0.25, y=0.2, width=0.5, height=0.4)

    assert roi.to_pixels(frame_width=1920, frame_height=1080) == (480, 216, 960, 432)


@pytest.mark.parametrize(
    "roi",
    [
        {"x": -0.01, "y": 0.0, "width": 0.5, "height": 0.5},
        {"x": 0.8, "y": 0.0, "width": 0.5, "height": 0.5},
        {"x": 0.0, "y": 0.9, "width": 0.5, "height": 0.2},
    ],
)
def test_roi_config_rejects_out_of_bounds_values(roi: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        ROIConfig(**roi)


def test_stream_and_auth_result_models_are_explicit() -> None:
    stream = StreamInfo(
        platform="bilibili",
        room_id="123",
        url="https://example.test/live.flv",
        format=StreamFormat.FLV,
        auth_mode=AuthMode.ANONYMOUS,
        ttl_seconds=60,
        requires_cookie=False,
    )
    frame = FramePacket(
        data=np.zeros((720, 1280, 3), dtype=np.uint8),
        received_at=time.perf_counter(),
        width=1280,
        height=720,
    )
    candidate = QRCandidate(
        payload="https://example.test/qr",
        detected_at=frame.received_at,
        source_frame_received_at=frame.received_at,
        roi=ROIConfig.full_frame(),
    )
    account = AccountRef(uid="10001", game_id=GameID.HONOR_OF_KINGS)
    scan = ScanResult(candidate=candidate, account=account, scan_token="scan-token")
    confirm = ConfirmResult(scan=scan, success=True, sent_at=time.perf_counter())

    assert stream.format is StreamFormat.FLV
    assert frame.width == 1280
    assert confirm.success is True


def test_sensitive_logging_values_are_redacted() -> None:
    event = scrub_sensitive_event(
        None,
        "info",
        {
            "event": "login",
            "token": "secret-token",
            "nested": {"cookie": "secret-cookie", "safe": "value"},
            "payload": "qr payload should also be hidden",
        },
    )

    assert event["token"] == "[REDACTED]"
    assert event["nested"]["cookie"] == "[REDACTED]"
    assert event["nested"]["safe"] == "value"
    assert event["payload"] == "[REDACTED]"


def test_sensitive_logging_redacts_compound_secret_keys() -> None:
    event = scrub_sensitive_event(
        None,
        "info",
        {
            "auth_token": "secret-token",
            "nested": {"user_cookie": "secret-cookie", "safe": "value"},
            "safe": "value",
        },
    )

    assert event["auth_token"] == "[REDACTED]"
    assert event["nested"]["user_cookie"] == "[REDACTED]"
    assert event["nested"]["safe"] == "value"
    assert event["safe"] == "value"


def test_sensitive_logging_redacts_nested_sequence_values() -> None:
    event = scrub_sensitive_event(
        None,
        "info",
        {
            "events": [
                {"token": "secret-token", "safe": "value"},
                {"headers": {"Cookie": "secret-cookie"}},
            ],
            "tuple_events": ({"qr_payload": "secret-payload"},),
        },
    )

    assert event["events"][0]["token"] == "[REDACTED]"
    assert event["events"][0]["safe"] == "value"
    assert event["events"][1]["headers"]["Cookie"] == "[REDACTED]"
    assert event["tuple_events"][0]["qr_payload"] == "[REDACTED]"


def test_sensitive_logging_redacts_tencent_identity_fields() -> None:
    event = scrub_sensitive_event(
        None,
        "info",
        {
            "account_id": "SECRET_ACCOUNT_ID",
            "stuid": "SECRET_STUID",
            "ltuid": "SECRET_LTUID",
            "mid": "SECRET_MID",
            "safe": "value",
        },
    )

    assert event["account_id"] == "[REDACTED]"
    assert event["stuid"] == "[REDACTED]"
    assert event["ltuid"] == "[REDACTED]"
    assert event["mid"] == "[REDACTED]"
    assert event["safe"] == "value"


def test_fake_account_store_uses_fixed_authorization_boundary() -> None:
    store = FakeAccountStore()
    account = AccountRef(uid="10001", game_id=GameID.HONOR_OF_KINGS)

    assert store.is_account_authorized(account.uid, account.game_id) is False

    store.save_token(account.game_id, account.uid, "stored-token", authorized=True)

    assert store.get_token(account.game_id, account.uid) == "stored-token"
    assert store.is_account_authorized(account.uid, account.game_id) is True

    store.delete_token(account.game_id, account.uid)

    assert store.get_token(account.game_id, account.uid) is None
    assert store.is_account_authorized(account.uid, account.game_id) is False
