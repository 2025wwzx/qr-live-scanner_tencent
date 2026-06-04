#!/usr/bin/env python3
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

from qr_live_scanner_tencent.interfaces import (
    AuthMode,
    FramePacket,
    StreamFormat,
    StreamInfo,
    StreamResolveError,
)
from qr_live_scanner_tencent.stream.ffmpeg import RawVideoFrameReader

DOUYIN_ENTER_PATH = "/webcast/room/web/enter/"
DEFAULT_BROWSER_TIMEOUT_SECONDS = 30.0
DEFAULT_CHROME_PATH = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")

try:
    from playwright.async_api import async_playwright
except ImportError:  # pragma: no cover - exercised through runtime error path.
    async_playwright = None  # type: ignore[assignment]


@dataclass(frozen=True, slots=True)
class BrowserResponse:
    """保存浏览器拦截到的抖音 enter 响应，不包含 Cookie。"""

    url: str
    payload: dict[str, Any]


class BrowserResolver(Protocol):
    async def capture_enter_response(self, room_url: str) -> BrowserResponse:
        """打开直播间并返回浏览器捕获到的 enter 响应。"""


class FrameReader(Protocol):
    def frames(self, stream_info: StreamInfo) -> AsyncIterator[FramePacket]:
        """Yield decoded frames for a stream."""


@dataclass(slots=True)
class DouyinBrowserStreamSource:
    browser_resolver: BrowserResolver
    ttl_seconds: int = 60
    frame_reader: FrameReader = field(default_factory=RawVideoFrameReader)

    async def resolve(self, room_id: str, auth_mode: AuthMode = AuthMode.AUTO) -> StreamInfo:
        room_url = _normalize_room_url(room_id)
        web_rid = extract_web_rid(room_url)
        try:
            response = await self.browser_resolver.capture_enter_response(room_url)
        except Exception as exc:
            msg = "Douyin browser capture failed"
            raise StreamResolveError(msg) from exc
        if not is_douyin_enter_url(response.url):
            msg = "Douyin browser did not capture enter response"
            raise StreamResolveError(msg)
        stream_url, stream_format = parse_douyin_enter_payload(response.payload)
        return StreamInfo(
            platform="douyin",
            room_id=web_rid,
            url=stream_url,
            format=stream_format,
            auth_mode=auth_mode,
            ttl_seconds=self.ttl_seconds,
            requires_cookie=False,
            headers=_stream_headers(),
        )

    async def frames(self, stream_info: StreamInfo) -> AsyncIterator[FramePacket]:
        async for frame in self.frame_reader.frames(stream_info):
            yield frame


@dataclass(slots=True)
class PlaywrightDouyinBrowserResolver:
    """使用 Playwright Chromium 打开抖音直播间，并拦截 enter 响应。"""

    user_data_dir: Path
    chrome_executable_path: Path | None = None
    timeout_seconds: float = DEFAULT_BROWSER_TIMEOUT_SECONDS
    headless: bool = False

    async def capture_enter_response(self, room_url: str) -> BrowserResponse:
        if async_playwright is None:
            msg = "Python Playwright is not installed"
            raise StreamResolveError(msg)
        timeout_ms = int(self.timeout_seconds * 1000)
        try:
            async with async_playwright() as playwright:
                if self.chrome_executable_path is None:
                    context = await playwright.chromium.launch_persistent_context(
                        user_data_dir=str(self.user_data_dir),
                        headless=self.headless,
                        viewport={"width": 1280, "height": 720},
                        accept_downloads=False,
                    )
                else:
                    context = await playwright.chromium.launch_persistent_context(
                        user_data_dir=str(self.user_data_dir),
                        executable_path=str(self.chrome_executable_path),
                        headless=self.headless,
                        viewport={"width": 1280, "height": 720},
                        accept_downloads=False,
                    )
                try:
                    page = context.pages[0] if context.pages else await context.new_page()
                    await page.goto(room_url, wait_until="domcontentloaded", timeout=timeout_ms)
                    response = await page.wait_for_event(
                        "response",
                        predicate=lambda item: is_douyin_enter_url(str(item.url)),
                        timeout=timeout_ms,
                    )
                    try:
                        payload = await response.json()
                    except ValueError as exc:
                        msg = "Douyin browser enter JSON failed"
                        raise StreamResolveError(msg) from exc
                    if not isinstance(payload, dict):
                        msg = "Douyin browser enter JSON failed"
                        raise StreamResolveError(msg)
                    return BrowserResponse(url=str(response.url), payload=payload)
                finally:
                    await context.close()
        except StreamResolveError:
            raise
        except Exception as exc:
            msg = "Douyin browser capture failed"
            raise StreamResolveError(msg) from exc


def is_douyin_enter_url(url: str) -> bool:
    parsed = urlparse(str(url))
    return (
        parsed.scheme in {"http", "https"}
        and parsed.netloc == "live.douyin.com"
        and (parsed.path == DOUYIN_ENTER_PATH.rstrip("/") or parsed.path == DOUYIN_ENTER_PATH)
    )


def extract_web_rid(value: str) -> str:
    normalized = str(value).strip()
    if not normalized or "\r" in normalized or "\n" in normalized:
        msg = "Douyin room id is required"
        raise StreamResolveError(msg)
    parsed = urlparse(normalized)
    if parsed.scheme and parsed.netloc:
        path = parsed.path.strip("/")
        if not path or "/" in path:
            msg = "Douyin room id is required"
            raise StreamResolveError(msg)
        return path
    return normalized


def parse_douyin_enter_payload(payload: dict[str, Any]) -> tuple[str, StreamFormat]:
    room_data = _room_data(payload)
    stream_url = room_data.get("stream_url") or {}
    flv = _first_url(stream_url.get("flv_pull_url"))
    if flv:
        return flv, StreamFormat.FLV
    hls = _first_url(stream_url.get("hls_pull_url_map"))
    if hls:
        return hls, StreamFormat.HLS
    raise StreamResolveError("Douyin browser response did not contain stream URLs")


def _normalize_room_url(value: str) -> str:
    web_rid = extract_web_rid(value)
    parsed = urlparse(str(value).strip())
    if parsed.scheme and parsed.netloc:
        return str(value).strip()
    return f"https://live.douyin.com/{web_rid}"


def _room_data(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data", {}).get("data", [])
    if not data:
        raise StreamResolveError("Douyin browser response returned no room data")
    room_data = data[0]
    if not isinstance(room_data, dict):
        raise StreamResolveError("Douyin browser response returned malformed room data")
    if _parse_room_status(room_data.get("status")) != 2:
        raise StreamResolveError("Douyin room is not live")
    return room_data


def _parse_room_status(value: object) -> int:
    if value is None:
        return 0
    if not isinstance(value, int | float | str):
        msg = "Douyin browser response returned malformed room status"
        raise StreamResolveError(msg)
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        msg = "Douyin browser response returned malformed room status"
        raise StreamResolveError(msg) from exc


def _first_url(value: object) -> str | None:
    if isinstance(value, str) and _looks_like_url(value):
        return value
    if isinstance(value, dict):
        for item in value.values():
            if isinstance(item, str) and _looks_like_url(item):
                return item
    return None


def _looks_like_url(value: str) -> bool:
    if not value.strip() or "\r" in value or "\n" in value:
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _stream_headers() -> dict[str, str]:
    return {
        "Referer": "https://live.douyin.com/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0 Safari/537.36"
        ),
    }
