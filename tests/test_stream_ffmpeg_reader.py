import asyncio
import subprocess
import time
from collections.abc import Iterator
from threading import Event, Thread

import numpy as np
import pytest

from qr_live_scanner_tencent.interfaces import AuthMode, StreamFormat, StreamInfo
from qr_live_scanner_tencent.stream.ffmpeg import (
    RawVideoFrameReader,
    build_ffmpeg_rawvideo_command,
    run_ffmpeg_raw_frames,
)


def fake_raw_frames(_command: list[str], frame_size: int, _stop_event: Event) -> Iterator[bytes]:
    first = np.full((2, 2, 3), 10, dtype=np.uint8).tobytes()
    second = np.full((2, 2, 3), 20, dtype=np.uint8).tobytes()
    assert len(first) == frame_size
    yield first
    time.sleep(0.03)
    yield second


def fast_thread_raw_frames(
    _command: list[str], frame_size: int, _stop_event: Event
) -> Iterator[bytes]:
    assert frame_size == 12
    for value in (10, 20, 30):
        yield np.full((2, 2, 3), value, dtype=np.uint8).tobytes()


class StopAwareStdout:
    def __init__(self, stop_event: Event) -> None:
        self.stop_event = stop_event

    def read(self, frame_size: int) -> bytes:
        self.stop_event.set()
        time.sleep(0.01)
        return b""


class RecordingProcess:
    def __init__(self, stop_event: Event) -> None:
        self.stdout = StopAwareStdout(stop_event)
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return None

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        return 0


class BlockingStdout:
    def __init__(self) -> None:
        self.entered = Event()
        self.release = Event()

    def read(self, frame_size: int) -> bytes:
        self.entered.set()
        self.release.wait(timeout=1.0)
        return b""


class BlockingProcess:
    def __init__(self) -> None:
        self.stdout = BlockingStdout()
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return None

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        return 0


@pytest.mark.asyncio
async def test_raw_video_frame_reader_yields_frame_packets() -> None:
    stream = StreamInfo(
        platform="bilibili",
        room_id="1",
        url="https://example/live.flv",
        format=StreamFormat.FLV,
        auth_mode=AuthMode.ANONYMOUS,
        ttl_seconds=60,
        requires_cookie=False,
    )
    reader = RawVideoFrameReader(width=2, height=2, raw_frame_runner=fake_raw_frames)

    frames = [frame async for frame in reader.frames(stream)]

    assert len(frames) == 2
    assert frames[0].width == 2
    assert frames[0].height == 2
    assert frames[0].sequence == 0
    assert frames[1].sequence == 1
    assert frames[0].data.shape == (2, 2, 3)
    assert int(frames[0].data[0, 0, 0]) == 10
    assert frames[0].received_at <= time.perf_counter()


@pytest.mark.asyncio
async def test_raw_video_frame_reader_keeps_only_latest_frame_when_consumer_lags() -> None:
    stream = StreamInfo(
        platform="bilibili",
        room_id="1",
        url="https://example/live.flv",
        format=StreamFormat.FLV,
        auth_mode=AuthMode.ANONYMOUS,
        ttl_seconds=60,
        requires_cookie=False,
    )
    reader = RawVideoFrameReader(
        width=2,
        height=2,
        raw_frame_runner=fast_thread_raw_frames,
        poll_interval_seconds=0.01,
    )

    frames = []
    async for frame in reader.frames(stream):
        frames.append(frame)
        await asyncio.sleep(0.02)

    assert len(frames) == 1
    assert frames[0].sequence == 2
    assert int(frames[0].data[0, 0, 0]) == 30


def test_ffmpeg_raw_frame_runner_terminates_process_when_stop_event_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stop_event = Event()
    process = RecordingProcess(stop_event)

    def fake_popen(
        command: list[str],
        *,
        stdout: int,
        stderr: int,
    ) -> RecordingProcess:
        assert command == ["ffmpeg"]
        assert stdout == subprocess.PIPE
        assert stderr == subprocess.DEVNULL
        return process

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    frames = list(run_ffmpeg_raw_frames(["ffmpeg"], frame_size=12, stop_event=stop_event))

    assert frames == []
    assert process.terminated is True
    assert process.killed is False


def test_ffmpeg_raw_frame_runner_interrupts_blocking_read_on_stop_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stop_event = Event()
    process = BlockingProcess()

    def fake_popen(
        command: list[str],
        *,
        stdout: int,
        stderr: int,
    ) -> BlockingProcess:
        return process

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    worker = Thread(
        target=lambda: list(
            run_ffmpeg_raw_frames(["ffmpeg"], frame_size=12, stop_event=stop_event)
        ),
        daemon=True,
    )
    worker.start()
    assert process.stdout.entered.wait(timeout=1.0) is True
    stop_event.set()
    try:
        time.sleep(0.05)
        assert process.terminated is True
    finally:
        process.stdout.release.set()
        worker.join(timeout=1.0)


def test_ffmpeg_command_includes_stream_headers() -> None:
    stream = StreamInfo(
        platform="douyin",
        room_id="1",
        url="https://example/live.flv",
        format=StreamFormat.FLV,
        auth_mode=AuthMode.COOKIE,
        ttl_seconds=60,
        requires_cookie=True,
        headers={
            "Referer": "https://live.douyin.com/",
            "User-Agent": "qr-live-scanner-tencent-test",
            "Cookie": "FAKE_COOKIE",
        },
    )

    command = build_ffmpeg_rawvideo_command(stream, width=2, height=2)

    header_index = command.index("-headers")
    header_block = command[header_index + 1]
    assert "Referer: https://live.douyin.com/\r\n" in header_block
    assert "User-Agent: qr-live-scanner-tencent-test\r\n" in header_block
    assert "Cookie: FAKE_COOKIE\r\n" in header_block
    assert command[header_index + 2] == "-i"


def test_ffmpeg_command_rejects_multiline_stream_headers() -> None:
    stream = StreamInfo(
        platform="douyin",
        room_id="1",
        url="https://example/live.flv",
        format=StreamFormat.FLV,
        auth_mode=AuthMode.COOKIE,
        ttl_seconds=60,
        requires_cookie=True,
        headers={"Cookie": "first\r\nInjected: second"},
    )

    with pytest.raises(ValueError, match="header"):
        build_ffmpeg_rawvideo_command(stream, width=2, height=2)


def test_ffmpeg_command_rejects_blank_stream_url() -> None:
    stream = StreamInfo(
        platform="bilibili",
        room_id="1",
        url="   ",
        format=StreamFormat.FLV,
        auth_mode=AuthMode.ANONYMOUS,
        ttl_seconds=60,
        requires_cookie=False,
    )

    with pytest.raises(ValueError, match="URL"):
        build_ffmpeg_rawvideo_command(stream, width=2, height=2)


def test_ffmpeg_command_rejects_multiline_stream_url() -> None:
    stream = StreamInfo(
        platform="bilibili",
        room_id="1",
        url="https://example/live.flv\r\nInjected: value",
        format=StreamFormat.FLV,
        auth_mode=AuthMode.ANONYMOUS,
        ttl_seconds=60,
        requires_cookie=False,
    )

    with pytest.raises(ValueError, match="URL"):
        build_ffmpeg_rawvideo_command(stream, width=2, height=2)


@pytest.mark.parametrize(("width", "height"), [(0, 2), (2, 0), (-1, 2), (2, -1)])
def test_ffmpeg_command_rejects_non_positive_dimensions(width: int, height: int) -> None:
    stream = StreamInfo(
        platform="bilibili",
        room_id="1",
        url="https://example/live.flv",
        format=StreamFormat.FLV,
        auth_mode=AuthMode.ANONYMOUS,
        ttl_seconds=60,
        requires_cookie=False,
    )

    with pytest.raises(ValueError, match="dimensions"):
        build_ffmpeg_rawvideo_command(stream, width=width, height=height)


@pytest.mark.asyncio
async def test_raw_video_frame_reader_rejects_non_positive_dimensions() -> None:
    stream = StreamInfo(
        platform="bilibili",
        room_id="1",
        url="https://example/live.flv",
        format=StreamFormat.FLV,
        auth_mode=AuthMode.ANONYMOUS,
        ttl_seconds=60,
        requires_cookie=False,
    )
    reader = RawVideoFrameReader(width=0, height=2, raw_frame_runner=fake_raw_frames)

    with pytest.raises(ValueError, match="dimensions"):
        _ = [frame async for frame in reader.frames(stream)]
