import statistics
import sys
import time
from collections.abc import Iterable
from types import SimpleNamespace

import numpy as np
import pytest
import qrcode
from PIL import Image

from qr_live_scanner_tencent.detection import DecoderChain, QRDeduplicator, ZxingDecoder, crop_roi
from qr_live_scanner_tencent.interfaces import FramePacket, QRCandidate, ROIConfig
from qr_live_scanner_tencent.stream import LatestFrameBuffer, get_ffmpeg_exe


class BlankPayloadBackend:
    name = "blank-test"

    def decode_array(self, image: np.ndarray) -> str | None:
        return "   "


def make_frame(payload: str, *, width: int = 1280, height: int = 720) -> FramePacket:
    image = Image.new("RGB", (width, height), "white")
    qr = qrcode.make(payload).convert("RGB").resize((220, 220))
    image.paste(qr, (width // 2 - 110, height // 2 - 110))
    data = np.asarray(image, dtype=np.uint8)
    return FramePacket(
        data=data,
        received_at=time.perf_counter(),
        width=width,
        height=height,
    )


def test_crop_roi_uses_normalized_coordinates() -> None:
    frame = make_frame("crop")
    roi = ROIConfig(x=0.4, y=0.35, width=0.2, height=0.3)

    cropped = crop_roi(frame.data, roi)

    assert cropped.shape[0] == 216
    assert cropped.shape[1] == 256


def test_crop_roi_keeps_tiny_frame_crops_non_empty() -> None:
    frame = np.zeros((2, 2, 3), dtype=np.uint8)
    roi = ROIConfig(x=0.49, y=0.49, width=0.01, height=0.01)

    cropped = crop_roi(frame, roi)

    assert cropped.shape == (1, 1, 3)


def test_decoder_chain_prefers_zxing_and_decodes_synthetic_roi() -> None:
    payload = "https://example.test/login?q=abc"
    frame = make_frame(payload)
    roi = ROIConfig(x=0.38, y=0.28, width=0.24, height=0.44)
    decoder = DecoderChain()

    candidate = decoder.decode(frame, roi)

    assert candidate is not None
    assert candidate.payload == payload
    assert candidate.roi == roi


def test_decoder_chain_ignores_blank_backend_payloads() -> None:
    frame = make_frame("ignored")
    decoder = DecoderChain(backends=[BlankPayloadBackend()])

    candidate = decoder.decode(frame, ROIConfig.full_frame())

    assert candidate is None


def test_zxing_decoder_uses_qr_only_fast_path(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []
    qr_format = object()

    class FakeBarcodeFormat:
        QRCode = qr_format

    def read_barcode(image: np.ndarray, **kwargs: object) -> SimpleNamespace:
        calls.append({"shape": image.shape, **kwargs})
        return SimpleNamespace(text="fast-payload")

    fake_zxingcpp = SimpleNamespace(
        BarcodeFormat=FakeBarcodeFormat,
        read_barcode=read_barcode,
    )
    monkeypatch.setitem(sys.modules, "zxingcpp", fake_zxingcpp)

    payload = ZxingDecoder().decode_array(np.zeros((16, 16), dtype=np.uint8))

    assert payload == "fast-payload"
    assert calls == [
        {
            "shape": (16, 16),
            "formats": qr_format,
            "try_rotate": False,
            "try_downscale": False,
            "try_invert": False,
        }
    ]


def test_qr_deduplicator_accepts_once_until_ttl_expires() -> None:
    now = 100.0
    dedup = QRDeduplicator(ttl_seconds=10.0, clock=lambda: now)
    candidate = QRCandidate(
        payload="same",
        detected_at=now,
        source_frame_received_at=now,
        roi=ROIConfig.full_frame(),
    )

    assert dedup.accept(candidate) is True
    assert dedup.accept(candidate) is False

    now = 111.0

    assert dedup.accept(candidate) is True


def test_qr_deduplicator_rejects_negative_ttl() -> None:
    with pytest.raises(ValueError, match="TTL"):
        QRDeduplicator(ttl_seconds=-1.0)


def test_latest_frame_buffer_keeps_only_newest_frame() -> None:
    buffer = LatestFrameBuffer()
    frames = [make_frame(f"frame-{index}") for index in range(3)]

    for frame in frames:
        buffer.put(frame)

    assert buffer.get_latest() is frames[-1]


def test_latest_frame_buffer_uses_instance_lock() -> None:
    first = LatestFrameBuffer()
    second = LatestFrameBuffer()

    assert first._lock is not second._lock


def test_bundled_ffmpeg_path_exists() -> None:
    path = get_ffmpeg_exe()

    assert path.exists()
    assert path.name.lower().startswith("ffmpeg")


def test_synthetic_decode_latency_p95_under_200ms() -> None:
    decoder = DecoderChain()
    roi = ROIConfig(x=0.38, y=0.28, width=0.24, height=0.44)
    frames = [make_frame(f"https://example.test/{index}") for index in range(30)]

    latencies_ms = list(_decode_latencies_ms(decoder, frames, roi))

    assert len(latencies_ms) == 30
    assert statistics.quantiles(latencies_ms, n=20)[18] < 200


def _decode_latencies_ms(
    decoder: DecoderChain, frames: Iterable[FramePacket], roi: ROIConfig
) -> Iterable[float]:
    for frame in frames:
        start = time.perf_counter()
        candidate = decoder.decode(frame, roi)
        end = time.perf_counter()
        assert candidate is not None
        yield (end - start) * 1000
