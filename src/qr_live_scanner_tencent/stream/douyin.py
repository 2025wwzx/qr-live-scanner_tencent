from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from qr_live_scanner_tencent.interfaces import (
    AuthMode,
    FramePacket,
    StreamFormat,
    StreamInfo,
    StreamResolveError,
)
from qr_live_scanner_tencent.stream.ffmpeg import RawVideoFrameReader

SignedEnterUrlFactory = Callable[[str], str]


class FrameReader(Protocol):
    def frames(self, stream_info: StreamInfo) -> AsyncIterator[FramePacket]:
        """Yield decoded frames for a stream."""


@dataclass(slots=True)
class DouyinStreamSource:
    client: httpx.AsyncClient = field(default_factory=httpx.AsyncClient)
    signed_enter_url_factory: SignedEnterUrlFactory | None = None
    ttl_seconds: int = 60
    cookie: str | None = None
    frame_reader: FrameReader = field(default_factory=RawVideoFrameReader)

    async def resolve(self, room_id: str, auth_mode: AuthMode = AuthMode.AUTO) -> StreamInfo:
        room_id = _require_room_id(room_id)
        if self.signed_enter_url_factory is None:
            msg = "Douyin source requires a signed enter URL factory for web anti-bot parameters"
            raise StreamResolveError(msg)

        headers = self._headers(auth_mode)
        signed_url = _require_signed_enter_url(self.signed_enter_url_factory(room_id))
        try:
            response = await self.client.get(signed_url, headers=headers)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            msg = "Douyin enter HTTP failed"
            raise StreamResolveError(msg) from exc
        payload = _response_json(response, "Douyin enter JSON failed")
        room_data = self._room_data(payload)
        stream_url, stream_format = self._select_stream_url(room_data)
        return StreamInfo(
            platform="douyin",
            room_id=room_id,
            url=stream_url,
            format=stream_format,
            auth_mode=auth_mode,
            ttl_seconds=self.ttl_seconds,
            requires_cookie=auth_mode is AuthMode.COOKIE,
            headers=headers,
        )

    async def frames(self, stream_info: StreamInfo) -> AsyncIterator[FramePacket]:
        async for frame in self.frame_reader.frames(stream_info):
            yield frame

    def _headers(self, auth_mode: AuthMode) -> dict[str, str]:
        headers = {
            "Referer": "https://live.douyin.com/",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0 Safari/537.36"
            ),
        }
        if auth_mode is AuthMode.COOKIE:
            cookie = (self.cookie or "").strip()
            if not cookie or "\r" in cookie or "\n" in cookie:
                msg = "Douyin Cookie auth mode requires a Cookie value"
                raise StreamResolveError(msg)
            headers["Cookie"] = cookie
        return headers

    @staticmethod
    def _room_data(payload: dict[str, Any]) -> dict[str, Any]:
        data = payload.get("data", {}).get("data", [])
        if not data:
            raise StreamResolveError("Douyin room returned no room data")
        room_data = data[0]
        if not isinstance(room_data, dict):
            raise StreamResolveError("Douyin room returned malformed room data")
        if _parse_room_status(room_data.get("status")) != 2:
            raise StreamResolveError("Douyin room is not live")
        return room_data

    @staticmethod
    def _select_stream_url(room_data: dict[str, Any]) -> tuple[str, StreamFormat]:
        stream_url = room_data.get("stream_url") or {}
        flv = _first_url(stream_url.get("flv_pull_url"))
        if flv:
            return flv, StreamFormat.FLV
        hls = _first_url(stream_url.get("hls_pull_url_map"))
        if hls:
            return hls, StreamFormat.HLS
        raise StreamResolveError("Douyin room data did not contain FLV or HLS stream URLs")


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


def _require_room_id(room_id: str) -> str:
    normalized = str(room_id).strip()
    if not normalized or "\r" in normalized or "\n" in normalized:
        msg = "Douyin room id is required"
        raise StreamResolveError(msg)
    return normalized


def _parse_room_status(value: object) -> int:
    if value is None:
        return 0
    if not isinstance(value, int | float | str):
        msg = "Douyin room returned malformed room status"
        raise StreamResolveError(msg)
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        msg = "Douyin room returned malformed room status"
        raise StreamResolveError(msg) from exc


def _require_signed_enter_url(url: object) -> str:
    normalized = str(url).strip()
    if (
        not normalized
        or "\r" in normalized
        or "\n" in normalized
        or not _looks_like_url(normalized)
    ):
        msg = "Douyin signed enter URL must be a single-line http(s) URL"
        raise StreamResolveError(msg)
    return normalized


def _response_json(response: httpx.Response, message: str) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise StreamResolveError(message) from exc
    if not isinstance(data, dict):
        raise StreamResolveError(message)
    return data
