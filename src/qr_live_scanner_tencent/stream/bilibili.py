from __future__ import annotations

from collections.abc import AsyncIterator
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

BILIBILI_ROOM_INIT_URL = "https://api.live.bilibili.com/room/v1/Room/room_init"
BILIBILI_PLAY_INFO_URL = "https://api.live.bilibili.com/xlive/web-room/v2/index/getRoomPlayInfo"
BILIBILI_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)


class FrameReader(Protocol):
    def frames(self, stream_info: StreamInfo) -> AsyncIterator[FramePacket]:
        """Yield decoded frames for a stream."""


@dataclass(slots=True)
class BilibiliStreamSource:
    client: httpx.AsyncClient = field(default_factory=httpx.AsyncClient)
    preferred_format: StreamFormat = StreamFormat.FLV
    fallback_format: StreamFormat = StreamFormat.HLS
    ttl_seconds: int = 60
    cookie: str | None = None
    frame_reader: FrameReader = field(default_factory=RawVideoFrameReader)

    async def resolve(self, room_id: str, auth_mode: AuthMode = AuthMode.AUTO) -> StreamInfo:
        room_id = _require_room_id(room_id)
        headers = self._headers(auth_mode, room_id=room_id)
        real_room_id = await self._resolve_real_room_id(room_id, headers=headers)
        try:
            response = await self.client.get(
                BILIBILI_PLAY_INFO_URL,
                params={
                    "room_id": real_room_id,
                    "protocol": "0,1",
                    "format": "0,1,2",
                    "codec": "0,1",
                    "qn": "10000",
                    "platform": "web",
                },
                headers=headers,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            msg = "Bilibili play info HTTP failed"
            raise StreamResolveError(msg) from exc
        payload = _response_json(response, "Bilibili play info JSON failed")
        if payload.get("code") != 0:
            msg = "Bilibili play info failed"
            raise StreamResolveError(msg)

        stream_url, stream_format = self._select_stream_url(payload)
        return StreamInfo(
            platform="bilibili",
            room_id=str(real_room_id),
            url=stream_url,
            format=stream_format,
            auth_mode=auth_mode,
            ttl_seconds=self.ttl_seconds,
            requires_cookie=auth_mode is AuthMode.COOKIE,
            headers=headers or None,
        )

    async def frames(self, stream_info: StreamInfo) -> AsyncIterator[FramePacket]:
        async for frame in self.frame_reader.frames(stream_info):
            yield frame

    async def _resolve_real_room_id(self, room_id: str, *, headers: dict[str, str]) -> str:
        try:
            response = await self.client.get(
                BILIBILI_ROOM_INIT_URL,
                params={"id": room_id},
                headers=headers,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            msg = "Bilibili room init HTTP failed"
            raise StreamResolveError(msg) from exc
        payload = _response_json(response, "Bilibili room init JSON failed")
        if payload.get("code") != 0:
            msg = "Bilibili room init failed"
            raise StreamResolveError(msg)
        data = payload.get("data") or {}
        if _parse_room_status(data.get("live_status")) != 1:
            raise StreamResolveError("Bilibili room is not live")
        real_room_id = data.get("room_id")
        if not real_room_id:
            raise StreamResolveError("Bilibili room has no real room id")
        return str(real_room_id)

    def _headers(self, auth_mode: AuthMode, *, room_id: str) -> dict[str, str]:
        headers = {
            "User-Agent": BILIBILI_USER_AGENT,
            "Referer": f"https://live.bilibili.com/{room_id}",
        }
        if auth_mode is not AuthMode.COOKIE:
            return headers
        cookie = (self.cookie or "").strip()
        if not cookie or "\r" in cookie or "\n" in cookie:
            msg = "Bilibili Cookie auth mode requires a Cookie value"
            raise StreamResolveError(msg)
        headers["Cookie"] = cookie
        return headers

    def _select_stream_url(self, payload: dict[str, Any]) -> tuple[str, StreamFormat]:
        candidates = list(_iter_stream_candidates(payload))
        for desired in (self.preferred_format, self.fallback_format):
            for url, stream_format in candidates:
                if stream_format is desired:
                    return url, stream_format
        if candidates:
            return candidates[0]
        raise StreamResolveError("Bilibili play info did not contain a supported stream URL")


def _iter_stream_candidates(payload: dict[str, Any]) -> list[tuple[str, StreamFormat]]:
    playurl = payload.get("data", {}).get("playurl_info", {}).get("playurl", {})
    results: list[tuple[str, StreamFormat]] = []
    for stream in playurl.get("stream", []) or []:
        for format_item in stream.get("format", []) or []:
            raw_format = str(format_item.get("format_name") or "").lower()
            stream_format = _classify_format(raw_format)
            for codec in format_item.get("codec", []) or []:
                base_url = str(codec.get("base_url") or "")
                for url_info in codec.get("url_info", []) or []:
                    host = str(url_info.get("host") or "")
                    extra = str(url_info.get("extra") or "")
                    candidate_url = f"{host}{base_url}{extra}"
                    if _looks_like_url(candidate_url):
                        results.append((candidate_url, stream_format))
    return results


def _classify_format(raw_format: str) -> StreamFormat:
    if "flv" in raw_format:
        return StreamFormat.FLV
    if "ts" in raw_format or "m3u8" in raw_format or "hls" in raw_format:
        return StreamFormat.HLS
    return StreamFormat.UNKNOWN


def _require_room_id(room_id: str) -> str:
    normalized = str(room_id).strip()
    if not normalized or "\r" in normalized or "\n" in normalized:
        msg = "Bilibili room id is required"
        raise StreamResolveError(msg)
    return normalized


def _parse_room_status(value: object) -> int:
    if value is None:
        return 0
    if not isinstance(value, int | float | str):
        msg = "Bilibili room returned malformed room status"
        raise StreamResolveError(msg)
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        msg = "Bilibili room returned malformed room status"
        raise StreamResolveError(msg) from exc


def _looks_like_url(value: str) -> bool:
    if not value.strip() or "\r" in value or "\n" in value:
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _response_json(response: httpx.Response, message: str) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise StreamResolveError(message) from exc
    if not isinstance(data, dict):
        raise StreamResolveError(message)
    return data
