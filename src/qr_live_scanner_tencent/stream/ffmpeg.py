from __future__ import annotations

import asyncio
import subprocess
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event

import imageio_ffmpeg
import numpy as np

from qr_live_scanner_tencent.interfaces import FramePacket, StreamFormat, StreamInfo
from qr_live_scanner_tencent.stream.latest import LatestFrameBuffer

RawFrameRunner = Callable[[list[str], int, Event], Iterator[bytes]]


def get_ffmpeg_exe() -> Path:
    return Path(imageio_ffmpeg.get_ffmpeg_exe())


def build_ffmpeg_rawvideo_command(
    stream: StreamInfo, *, width: int = 1280, height: int = 720
) -> list[str]:
    _validate_dimensions(width, height)
    stream_url = _validate_stream_url(stream.url)
    command = [
        str(get_ffmpeg_exe()),
        "-hide_banner",
        "-loglevel",
        "warning",
    ]
    if stream.format is StreamFormat.FLV:
        command.extend(["-fflags", "nobuffer", "-flags", "low_delay"])
    elif stream.format is StreamFormat.HLS:
        command.extend(["-live_start_index", "-1"])
    if stream.headers:
        command.extend(["-headers", _format_ffmpeg_headers(stream.headers)])
    command.extend(
        [
            "-i",
            stream_url,
            "-vf",
            f"scale={width}:{height}",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "pipe:1",
        ]
    )
    return command


def _format_ffmpeg_headers(headers: dict[str, str]) -> str:
    lines: list[str] = []
    for name, value in headers.items():
        if not name or "\r" in name or "\n" in name or "\r" in value or "\n" in value:
            msg = "stream header names and values must be single-line"
            raise ValueError(msg)
        lines.append(f"{name}: {value}\r\n")
    return "".join(lines)


def _validate_dimensions(width: int, height: int) -> None:
    if width <= 0 or height <= 0:
        msg = "stream dimensions must be positive"
        raise ValueError(msg)


def _validate_stream_url(url: str) -> str:
    normalized = str(url).strip()
    if not normalized or "\r" in normalized or "\n" in normalized:
        msg = "stream URL must be a single-line value"
        raise ValueError(msg)
    return normalized


def run_ffmpeg_raw_frames(
    command: list[str], frame_size: int, stop_event: Event
) -> Iterator[bytes]:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if process.stdout is None:
        return
    watcher = threading.Thread(
        target=_terminate_on_stop,
        args=(process, stop_event),
        name="qr-live-scanner-tencent-ffmpeg-stop",
        daemon=True,
    )
    watcher.start()
    try:
        while not stop_event.is_set():
            chunk = process.stdout.read(frame_size)
            if len(chunk) != frame_size:
                break
            yield chunk
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        watcher.join(timeout=1.0)


def _terminate_on_stop(process: subprocess.Popen[bytes], stop_event: Event) -> None:
    stop_event.wait()
    if process.poll() is None:
        process.terminate()


@dataclass(slots=True)
class RawVideoFrameReader:
    width: int = 1280
    height: int = 720
    raw_frame_runner: RawFrameRunner = run_ffmpeg_raw_frames
    poll_interval_seconds: float = 0.005
    latest_buffer: LatestFrameBuffer = field(default_factory=LatestFrameBuffer)

    async def frames(self, stream: StreamInfo) -> AsyncIterator[FramePacket]:
        frame_size = self.width * self.height * 3
        command = build_ffmpeg_rawvideo_command(stream, width=self.width, height=self.height)
        stop_event = Event()
        done_event = Event()
        self.latest_buffer.clear()
        producer = threading.Thread(
            target=self._produce_frames,
            args=(command, frame_size, stop_event, done_event),
            name="qr-live-scanner-tencent-ffmpeg-reader",
            daemon=True,
        )
        producer.start()
        last_sequence = -1
        try:
            while producer.is_alive() or self.latest_buffer.get_latest() is not None:
                frame = self.latest_buffer.get_latest()
                if frame is not None and frame.sequence != last_sequence:
                    last_sequence = frame.sequence
                    yield frame
                if done_event.is_set() and (frame is None or frame.sequence == last_sequence):
                    break
                await asyncio.sleep(self.poll_interval_seconds)
        finally:
            stop_event.set()
            producer.join(timeout=1.0)

    def _produce_frames(
        self,
        command: list[str],
        frame_size: int,
        stop_event: Event,
        done_event: Event,
    ) -> None:
        sequence = 0
        try:
            for raw_frame in self.raw_frame_runner(command, frame_size, stop_event):
                if stop_event.is_set():
                    break
                if len(raw_frame) != frame_size:
                    continue
                data = np.frombuffer(raw_frame, dtype=np.uint8).reshape(
                    (self.height, self.width, 3)
                )
                self.latest_buffer.put(
                    FramePacket(
                        data=data.copy(),
                        received_at=time.perf_counter(),
                        width=self.width,
                        height=self.height,
                        sequence=sequence,
                    )
                )
                sequence += 1
        finally:
            done_event.set()
