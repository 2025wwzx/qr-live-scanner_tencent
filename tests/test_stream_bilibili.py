from collections.abc import AsyncIterator

import httpx
import numpy as np
import pytest
import respx

from qr_live_scanner_tencent.interfaces import (
    AuthMode,
    FramePacket,
    StreamFormat,
    StreamInfo,
    StreamResolveError,
)
from qr_live_scanner_tencent.stream.bilibili import BilibiliStreamSource


@pytest.mark.asyncio
@respx.mock
async def test_bilibili_source_resolves_preferred_flv_stream() -> None:
    client = httpx.AsyncClient()
    source = BilibiliStreamSource(client=client)
    respx.get("https://api.live.bilibili.com/room/v1/Room/room_init").mock(
        return_value=httpx.Response(
            200,
            json={"code": 0, "data": {"room_id": 456, "live_status": 1}},
        )
    )
    respx.get("https://api.live.bilibili.com/xlive/web-room/v2/index/getRoomPlayInfo").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "playurl_info": {
                        "playurl": {
                            "stream": [
                                {
                                    "format": [
                                        {
                                            "format_name": "flv",
                                            "codec": [
                                                {
                                                    "base_url": "live.flv",
                                                    "url_info": [
                                                        {
                                                            "host": "https://cdn.example/",
                                                            "extra": "?token=abc",
                                                        }
                                                    ],
                                                }
                                            ],
                                        }
                                    ]
                                }
                            ]
                        }
                    }
                },
            },
        )
    )

    info = await source.resolve("123", AuthMode.ANONYMOUS)

    assert info.platform == "bilibili"
    assert info.room_id == "456"
    assert info.url == "https://cdn.example/live.flv?token=abc"
    assert info.format is StreamFormat.FLV
    assert info.requires_cookie is False
    requests = respx.calls
    assert requests[0].request.headers["User-Agent"].startswith("Mozilla/5.0")
    assert requests[0].request.headers["Referer"] == "https://live.bilibili.com/123"
    assert requests[1].request.headers["Referer"] == "https://live.bilibili.com/123"
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_bilibili_source_falls_back_to_hls() -> None:
    client = httpx.AsyncClient()
    source = BilibiliStreamSource(client=client)
    respx.get("https://api.live.bilibili.com/room/v1/Room/room_init").mock(
        return_value=httpx.Response(
            200,
            json={"code": 0, "data": {"room_id": 456, "live_status": 1}},
        )
    )
    respx.get("https://api.live.bilibili.com/xlive/web-room/v2/index/getRoomPlayInfo").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "playurl_info": {
                        "playurl": {
                            "stream": [
                                {
                                    "format": [
                                        {
                                            "format_name": "ts",
                                            "codec": [
                                                {
                                                    "base_url": "playlist.m3u8",
                                                    "url_info": [
                                                        {
                                                            "host": "https://cdn.example/",
                                                            "extra": "?token=hls",
                                                        }
                                                    ],
                                                }
                                            ],
                                        }
                                    ]
                                }
                            ]
                        }
                    }
                },
            },
        )
    )

    info = await source.resolve("123", AuthMode.ANONYMOUS)

    assert info.url == "https://cdn.example/playlist.m3u8?token=hls"
    assert info.format is StreamFormat.HLS
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_bilibili_source_requires_cookie_value_for_cookie_auth() -> None:
    client = httpx.AsyncClient()
    source = BilibiliStreamSource(client=client)

    with pytest.raises(StreamResolveError, match="Cookie"):
        await source.resolve("123", AuthMode.COOKIE)

    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_bilibili_source_rejects_multiline_cookie_before_http() -> None:
    client = httpx.AsyncClient()
    source = BilibiliStreamSource(client=client, cookie="SESSDATA=SECRET\r\nInjected: value")

    with pytest.raises(StreamResolveError, match="Cookie") as exc_info:
        await source.resolve("123", AuthMode.COOKIE)

    message = str(exc_info.value)
    assert "SECRET" not in message
    assert "Injected" not in message
    assert len(respx.calls) == 0
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_bilibili_source_passes_cookie_header_when_configured() -> None:
    client = httpx.AsyncClient()
    source = BilibiliStreamSource(client=client, cookie="FAKE_COOKIE")
    respx.get("https://api.live.bilibili.com/room/v1/Room/room_init").mock(
        return_value=httpx.Response(
            200,
            json={"code": 0, "data": {"room_id": 456, "live_status": 1}},
        )
    )
    respx.get("https://api.live.bilibili.com/xlive/web-room/v2/index/getRoomPlayInfo").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "playurl_info": {
                        "playurl": {
                            "stream": [
                                {
                                    "format": [
                                        {
                                            "format_name": "flv",
                                            "codec": [
                                                {
                                                    "base_url": "live.flv",
                                                    "url_info": [
                                                        {
                                                            "host": "https://cdn.example/",
                                                            "extra": "",
                                                        }
                                                    ],
                                                }
                                            ],
                                        }
                                    ]
                                }
                            ]
                        }
                    }
                },
            },
        )
    )

    info = await source.resolve("123", AuthMode.COOKIE)

    assert info.requires_cookie is True
    assert info.headers is not None
    assert info.headers["Cookie"] == "FAKE_COOKIE"
    assert info.headers["User-Agent"].startswith("Mozilla/5.0")
    assert info.headers["Referer"] == "https://live.bilibili.com/123"
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_bilibili_source_rejects_blank_room_id_before_http() -> None:
    client = httpx.AsyncClient()
    source = BilibiliStreamSource(client=client)

    with pytest.raises(StreamResolveError, match="room id"):
        await source.resolve("   ", AuthMode.ANONYMOUS)

    assert len(respx.calls) == 0
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_bilibili_source_rejects_multiline_room_id_before_http() -> None:
    client = httpx.AsyncClient()
    source = BilibiliStreamSource(client=client)

    with pytest.raises(StreamResolveError, match="room id") as exc_info:
        await source.resolve("123\r\nCookie=SECRET_COOKIE_VALUE", AuthMode.ANONYMOUS)

    message = str(exc_info.value)
    assert "SECRET_COOKIE_VALUE" not in message
    assert "Cookie" not in message
    assert len(respx.calls) == 0
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_bilibili_source_reports_not_live() -> None:
    client = httpx.AsyncClient()
    source = BilibiliStreamSource(client=client)
    respx.get("https://api.live.bilibili.com/room/v1/Room/room_init").mock(
        return_value=httpx.Response(
            200,
            json={"code": 0, "data": {"room_id": 456, "live_status": 0}},
        )
    )

    with pytest.raises(StreamResolveError, match="not live"):
        await source.resolve("123", AuthMode.ANONYMOUS)

    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_bilibili_source_wraps_malformed_live_status() -> None:
    client = httpx.AsyncClient()
    source = BilibiliStreamSource(client=client)
    respx.get("https://api.live.bilibili.com/room/v1/Room/room_init").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "room_id": 456,
                    "live_status": "SECRET_COOKIE_VALUE",
                },
            },
        )
    )

    with pytest.raises(StreamResolveError, match="malformed room status") as exc_info:
        await source.resolve("123", AuthMode.ANONYMOUS)

    message = str(exc_info.value)
    assert "SECRET_COOKIE_VALUE" not in message
    assert "cookie" not in message.lower()
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_bilibili_source_does_not_echo_room_id_in_status_errors() -> None:
    client = httpx.AsyncClient()
    source = BilibiliStreamSource(client=client)
    sensitive_room_id = "12345?SESSDATA=SECRET_COOKIE_VALUE"
    respx.get("https://api.live.bilibili.com/room/v1/Room/room_init").mock(
        return_value=httpx.Response(
            200,
            json={"code": 0, "data": {"room_id": 456, "live_status": 0}},
        )
    )

    with pytest.raises(StreamResolveError, match="not live") as exc_info:
        await source.resolve(sensitive_room_id, AuthMode.ANONYMOUS)

    message = str(exc_info.value)
    assert sensitive_room_id not in message
    assert "SECRET_COOKIE_VALUE" not in message
    assert "SESSDATA" not in message
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_bilibili_source_wraps_room_init_http_errors() -> None:
    client = httpx.AsyncClient()
    source = BilibiliStreamSource(client=client)
    respx.get("https://api.live.bilibili.com/room/v1/Room/room_init").mock(
        return_value=httpx.Response(412)
    )

    with pytest.raises(StreamResolveError, match="Bilibili room init HTTP failed"):
        await source.resolve("123", AuthMode.ANONYMOUS)

    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_bilibili_source_wraps_room_init_json_errors_without_body() -> None:
    client = httpx.AsyncClient()
    source = BilibiliStreamSource(client=client)
    respx.get("https://api.live.bilibili.com/room/v1/Room/room_init").mock(
        return_value=httpx.Response(200, content=b"SECRET_COOKIE_VALUE <html>")
    )

    with pytest.raises(StreamResolveError, match="Bilibili room init JSON failed") as exc_info:
        await source.resolve("123", AuthMode.ANONYMOUS)

    message = str(exc_info.value)
    assert "SECRET_COOKIE_VALUE" not in message
    assert "html" not in message.lower()
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_bilibili_source_hides_room_init_platform_message() -> None:
    client = httpx.AsyncClient()
    source = BilibiliStreamSource(client=client)
    respx.get("https://api.live.bilibili.com/room/v1/Room/room_init").mock(
        return_value=httpx.Response(
            200,
            json={"code": -1, "message": "SECRET_COOKIE_VALUE platform detail"},
        )
    )

    with pytest.raises(StreamResolveError, match="Bilibili room init failed") as exc_info:
        await source.resolve("123", AuthMode.ANONYMOUS)

    message = str(exc_info.value)
    assert "SECRET_COOKIE_VALUE" not in message
    assert "platform detail" not in message
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_bilibili_source_wraps_play_info_http_errors() -> None:
    client = httpx.AsyncClient()
    source = BilibiliStreamSource(client=client)
    respx.get("https://api.live.bilibili.com/room/v1/Room/room_init").mock(
        return_value=httpx.Response(
            200,
            json={"code": 0, "data": {"room_id": 456, "live_status": 1}},
        )
    )
    respx.get("https://api.live.bilibili.com/xlive/web-room/v2/index/getRoomPlayInfo").mock(
        return_value=httpx.Response(503)
    )

    with pytest.raises(StreamResolveError, match="Bilibili play info HTTP failed"):
        await source.resolve("123", AuthMode.ANONYMOUS)

    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_bilibili_source_hides_play_info_platform_message() -> None:
    client = httpx.AsyncClient()
    source = BilibiliStreamSource(client=client)
    respx.get("https://api.live.bilibili.com/room/v1/Room/room_init").mock(
        return_value=httpx.Response(
            200,
            json={"code": 0, "data": {"room_id": 456, "live_status": 1}},
        )
    )
    respx.get("https://api.live.bilibili.com/xlive/web-room/v2/index/getRoomPlayInfo").mock(
        return_value=httpx.Response(
            200,
            json={"code": -1, "msg": "SECRET_TOKEN_VALUE platform detail"},
        )
    )

    with pytest.raises(StreamResolveError, match="Bilibili play info failed") as exc_info:
        await source.resolve("123", AuthMode.ANONYMOUS)

    message = str(exc_info.value)
    assert "SECRET_TOKEN_VALUE" not in message
    assert "platform detail" not in message
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_bilibili_source_wraps_play_info_json_errors_without_body() -> None:
    client = httpx.AsyncClient()
    source = BilibiliStreamSource(client=client)
    respx.get("https://api.live.bilibili.com/room/v1/Room/room_init").mock(
        return_value=httpx.Response(
            200,
            json={"code": 0, "data": {"room_id": 456, "live_status": 1}},
        )
    )
    respx.get("https://api.live.bilibili.com/xlive/web-room/v2/index/getRoomPlayInfo").mock(
        return_value=httpx.Response(200, content=b"SECRET_TOKEN_VALUE <html>")
    )

    with pytest.raises(StreamResolveError, match="Bilibili play info JSON failed") as exc_info:
        await source.resolve("123", AuthMode.ANONYMOUS)

    message = str(exc_info.value)
    assert "SECRET_TOKEN_VALUE" not in message
    assert "html" not in message.lower()
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_bilibili_source_rejects_invalid_stream_urls() -> None:
    client = httpx.AsyncClient()
    source = BilibiliStreamSource(client=client)
    respx.get("https://api.live.bilibili.com/room/v1/Room/room_init").mock(
        return_value=httpx.Response(
            200,
            json={"code": 0, "data": {"room_id": 456, "live_status": 1}},
        )
    )
    respx.get("https://api.live.bilibili.com/xlive/web-room/v2/index/getRoomPlayInfo").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "playurl_info": {
                        "playurl": {
                            "stream": [
                                {
                                    "format": [
                                        {
                                            "format_name": "flv",
                                            "codec": [
                                                {
                                                    "base_url": "live.flv\r\nInjected: value",
                                                    "url_info": [
                                                        {
                                                            "host": "https://SECRET_TOKEN.example/",
                                                            "extra": "",
                                                        },
                                                        {
                                                            "host": "ftp://cdn.example/",
                                                            "extra": "",
                                                        },
                                                    ],
                                                }
                                            ],
                                        }
                                    ]
                                }
                            ]
                        }
                    }
                },
            },
        )
    )

    with pytest.raises(StreamResolveError, match="supported stream URL") as exc_info:
        await source.resolve("123", AuthMode.ANONYMOUS)

    message = str(exc_info.value)
    assert "SECRET_TOKEN" not in message
    assert "Injected" not in message
    assert "ftp://" not in message
    await client.aclose()


class FakeFrameReader:
    def __init__(self) -> None:
        self.stream_urls: list[str] = []

    async def frames(self, stream_info: StreamInfo) -> AsyncIterator[FramePacket]:
        self.stream_urls.append(stream_info.url)
        yield FramePacket(
            data=np.zeros((2, 2, 3), dtype=np.uint8),
            received_at=1.0,
            width=2,
            height=2,
        )


@pytest.mark.asyncio
async def test_bilibili_frames_delegate_to_configured_frame_reader() -> None:
    reader = FakeFrameReader()
    source = BilibiliStreamSource(frame_reader=reader)
    stream = StreamInfo(
        platform="bilibili",
        room_id="456",
        url="https://cdn.example/live.flv",
        format=StreamFormat.FLV,
        auth_mode=AuthMode.ANONYMOUS,
        ttl_seconds=60,
        requires_cookie=False,
    )

    frames = [frame async for frame in source.frames(stream)]

    assert len(frames) == 1
    assert frames[0].width == 2
    assert reader.stream_urls == ["https://cdn.example/live.flv"]
