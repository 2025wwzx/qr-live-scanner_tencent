from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

import cv2
import numpy as np

from qr_live_scanner_tencent.detection.roi import crop_roi
from qr_live_scanner_tencent.interfaces import DecodeError, FramePacket, QRCandidate, ROIConfig


class DecoderBackend(Protocol):
    @property
    def name(self) -> str:
        """Human-readable decoder backend name."""

    def decode_array(self, image: np.ndarray) -> str | None:
        """Decode one QR payload from a cropped image."""


@dataclass(frozen=True, slots=True)
class ZxingDecoder:
    name: str = "zxing-cpp"
    qr_only: bool = True
    try_rotate: bool = False
    try_downscale: bool = False
    try_invert: bool = False

    def decode_array(self, image: np.ndarray) -> str | None:
        try:
            import zxingcpp
        except ImportError as exc:
            raise DecodeError("zxing-cpp is not installed") from exc

        if self.qr_only:
            result: Any | None = zxingcpp.read_barcode(
                image,
                formats=zxingcpp.BarcodeFormat.QRCode,
                try_rotate=self.try_rotate,
                try_downscale=self.try_downscale,
                try_invert=self.try_invert,
            )
            if result is None:
                return None
            return str(result.text)

        results: list[Any] = zxingcpp.read_barcodes(
            image,
            try_rotate=self.try_rotate,
            try_downscale=self.try_downscale,
            try_invert=self.try_invert,
        )
        if not results:
            return None
        return str(results[0].text)


@dataclass(frozen=True, slots=True)
class PyzbarDecoder:
    name: str = "pyzbar"

    def decode_array(self, image: np.ndarray) -> str | None:
        try:
            from pyzbar.pyzbar import decode
        except ImportError as exc:
            raise DecodeError("pyzbar is not installed") from exc

        results = decode(image)
        if not results:
            return None
        return str(results[0].data.decode("utf-8"))


class DecoderChain:
    def __init__(self, backends: list[DecoderBackend] | None = None) -> None:
        self.backends = backends or [ZxingDecoder(), PyzbarDecoder()]

    def decode(self, frame: FramePacket, roi: ROIConfig) -> QRCandidate | None:
        image = crop_roi(frame.data, roi)
        image = _to_grayscale(image)
        for backend in self.backends:
            try:
                payload = backend.decode_array(image)
            except DecodeError:
                continue
            normalized_payload = str(payload or "").strip()
            if normalized_payload:
                return QRCandidate(
                    payload=normalized_payload,
                    detected_at=time.perf_counter(),
                    source_frame_received_at=frame.received_at,
                    roi=roi,
                    backend=backend.name,
                )
        return None


def _to_grayscale(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
