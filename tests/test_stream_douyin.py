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
from qr_live_scanner_tencent.stream.douyin import DouyinStreamSource


def signed_url(web_rid: str) -> str:
    return f"https://live.douyin.com/webcast/room/web/enter/?web_rid={web_rid}&a_bogus=signed"


@pytest.mark.asyncio
@respx.mock
async def test_douyin_source_resolves_preferred_flv_stream() -> None:
    client = httpx.AsyncClient()
    source = DouyinStreamSource(client=client, signed_enter_url_factory=signed_url)
    respx.get("https://live.douyin.com/webcast/room/web/enter/").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "data": [
                        {
                            "status": 2,
                            "stream_url": {
                                "flv_pull_url": {"FULL_HD1": "https://cdn.example/live.flv"},
                                "hls_pull_url_map": {"FULL_HD1": "https://cdn.example/live.m3u8"},
                            },
                        }
                    ]
                }
            },
        )
    )

    info = await source.resolve("335354047186", AuthMode.ANONYMOUS)

    assert info.platform == "douyin"
    assert info.room_id == "335354047186"
    assert info.url == "https://cdn.example/live.flv"
    assert info.format is StreamFormat.FLV
    assert info.requires_cookie is False
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_douyin_source_falls_back_to_hls_stream() -> None:
    client = httpx.AsyncClient()
    source = DouyinStreamSource(client=client, signed_enter_url_factory=signed_url)
    respx.get("https://live.douyin.com/webcast/room/web/enter/").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "data": [
                        {
                            "status": 2,
                            "stream_url": {
                                "flv_pull_url": {},
                                "hls_pull_url_map": {"FULL_HD1": "https://cdn.example/live.m3u8"},
                            },
                        }
                    ]
                }
            },
        )
    )

    info = await source.resolve("335354047186", AuthMode.ANONYMOUS)

    assert info.url == "https://cdn.example/live.m3u8"
    assert info.format is StreamFormat.HLS
    await client.aclose()


@pytest.mark.asyncio
async def test_douyin_source_requires_cookie_value_for_cookie_auth() -> None:
    client = httpx.AsyncClient()
    source = DouyinStreamSource(client=client, signed_enter_url_factory=signed_url)

    with pytest.raises(StreamResolveError, match="Cookie"):
        await source.resolve("335354047186", AuthMode.COOKIE)

    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_douyin_source_rejects_multiline_cookie_before_http() -> None:
    client = httpx.AsyncClient()
    source = DouyinStreamSource(
        client=client,
        signed_enter_url_factory=signed_url,
        cookie="SESSDATA=SECRET\r\nInjected: value",
    )

    with pytest.raises(StreamResolveError, match="Cookie") as exc_info:
        await source.resolve("335354047186", AuthMode.COOKIE)

    message = str(exc_info.value)
    assert "SECRET" not in message
    assert "Injected" not in message
    assert len(respx.calls) == 0
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_douyin_source_passes_cookie_header_when_configured() -> None:
    client = httpx.AsyncClient()
    source = DouyinStreamSource(
        client=client,
        signed_enter_url_factory=signed_url,
        cookie="FAKE_COOKIE",
    )
    respx.get("https://live.douyin.com/webcast/room/web/enter/").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "data": [
                        {
                            "status": 2,
                            "stream_url": {
                                "flv_pull_url": {"FULL_HD1": "https://cdn.example/live.flv"},
                            },
                        }
                    ]
                }
            },
        )
    )

    info = await source.resolve("335354047186", AuthMode.COOKIE)

    assert info.requires_cookie is True
    assert info.headers is not None
    assert info.headers["Cookie"] == "FAKE_COOKIE"
    await client.aclose()


@pytest.mark.asyncio
async def test_douyin_source_requires_signed_url_factory() -> None:
    source = DouyinStreamSource(client=httpx.AsyncClient())

    with pytest.raises(StreamResolveError, match="signed enter URL factory"):
        await source.resolve("335354047186", AuthMode.ANONYMOUS)

    await source.client.aclose()


@pytest.mark.asyncio
async def test_douyin_source_rejects_blank_room_id_before_signed_url_factory() -> None:
    called_room_ids: list[str] = []

    def recording_signed_url(web_rid: str) -> str:
        called_room_ids.append(web_rid)
        return signed_url(web_rid)

    source = DouyinStreamSource(
        client=httpx.AsyncClient(),
        signed_enter_url_factory=recording_signed_url,
    )

    with pytest.raises(StreamResolveError, match="room id"):
        await source.resolve("   ", AuthMode.ANONYMOUS)

    assert called_room_ids == []
    await source.client.aclose()


@pytest.mark.asyncio
async def test_douyin_source_rejects_multiline_room_id_before_signed_url_factory() -> None:
    called_room_ids: list[str] = []

    def recording_signed_url(web_rid: str) -> str:
        called_room_ids.append(web_rid)
        return signed_url(web_rid)

    source = DouyinStreamSource(
        client=httpx.AsyncClient(),
        signed_enter_url_factory=recording_signed_url,
    )

    with pytest.raises(StreamResolveError, match="room id") as exc_info:
        await source.resolve("335354047186\ncookie=SECRET_COOKIE_VALUE", AuthMode.ANONYMOUS)

    message = str(exc_info.value)
    assert "SECRET_COOKIE_VALUE" not in message
    assert "cookie" not in message.lower()
    assert called_room_ids == []
    await source.client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_douyin_source_rejects_invalid_signed_enter_url_before_http() -> None:
    client = httpx.AsyncClient()
    sensitive_url = "ftp://SECRET_TOKEN_VALUE.example/live\nhttps://live.douyin.com/"
    source = DouyinStreamSource(
        client=client,
        signed_enter_url_factory=lambda _web_rid: sensitive_url,
    )

    with pytest.raises(StreamResolveError, match="signed enter URL") as exc_info:
        await source.resolve("335354047186", AuthMode.ANONYMOUS)

    message = str(exc_info.value)
    assert "SECRET_TOKEN_VALUE" not in message
    assert "ftp://" not in message
    assert len(respx.calls) == 0
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_douyin_source_reports_not_live() -> None:
    client = httpx.AsyncClient()
    source = DouyinStreamSource(client=client, signed_enter_url_factory=signed_url)
    respx.get("https://live.douyin.com/webcast/room/web/enter/").mock(
        return_value=httpx.Response(200, json={"data": {"data": [{"status": 4}]}})
    )

    with pytest.raises(StreamResolveError, match="not live"):
        await source.resolve("335354047186", AuthMode.ANONYMOUS)

    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_douyin_source_wraps_malformed_live_status() -> None:
    client = httpx.AsyncClient()
    source = DouyinStreamSource(client=client, signed_enter_url_factory=signed_url)
    respx.get("https://live.douyin.com/webcast/room/web/enter/").mock(
        return_value=httpx.Response(
            200,
            json={"data": {"data": [{"status": "SECRET_COOKIE_VALUE"}]}},
        )
    )

    with pytest.raises(StreamResolveError, match="malformed room status") as exc_info:
        await source.resolve("335354047186", AuthMode.ANONYMOUS)

    message = str(exc_info.value)
    assert "SECRET_COOKIE_VALUE" not in message
    assert "cookie" not in message.lower()
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_douyin_source_does_not_echo_room_id_in_room_data_errors() -> None:
    client = httpx.AsyncClient()
    source = DouyinStreamSource(client=client, signed_enter_url_factory=signed_url)
    sensitive_room_id = "335354047186?cookie=SECRET_COOKIE_VALUE"
    respx.get("https://live.douyin.com/webcast/room/web/enter/").mock(
        return_value=httpx.Response(200, json={"data": {"data": []}})
    )

    with pytest.raises(StreamResolveError, match="room data") as exc_info:
        await source.resolve(sensitive_room_id, AuthMode.ANONYMOUS)

    message = str(exc_info.value)
    assert sensitive_room_id not in message
    assert "SECRET_COOKIE_VALUE" not in message
    assert "cookie" not in message.lower()
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_douyin_source_wraps_enter_http_errors() -> None:
    client = httpx.AsyncClient()
    source = DouyinStreamSource(client=client, signed_enter_url_factory=signed_url)
    respx.get("https://live.douyin.com/webcast/room/web/enter/").mock(
        return_value=httpx.Response(403)
    )

    with pytest.raises(StreamResolveError, match="Douyin enter HTTP failed"):
        await source.resolve("335354047186", AuthMode.ANONYMOUS)

    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_douyin_source_wraps_enter_json_errors_without_body() -> None:
    client = httpx.AsyncClient()
    source = DouyinStreamSource(client=client, signed_enter_url_factory=signed_url)
    respx.get("https://live.douyin.com/webcast/room/web/enter/").mock(
        return_value=httpx.Response(200, content=b"SECRET_PAYLOAD_VALUE <html>")
    )

    with pytest.raises(StreamResolveError, match="Douyin enter JSON failed") as exc_info:
        await source.resolve("335354047186", AuthMode.ANONYMOUS)

    message = str(exc_info.value)
    assert "SECRET_PAYLOAD_VALUE" not in message
    assert "html" not in message.lower()
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_douyin_source_rejects_multiline_stream_urls() -> None:
    client = httpx.AsyncClient()
    source = DouyinStreamSource(client=client, signed_enter_url_factory=signed_url)
    respx.get("https://live.douyin.com/webcast/room/web/enter/").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "data": [
                        {
                            "status": 2,
                            "stream_url": {
                                "flv_pull_url": {
                                    "FULL_HD1": "https://cdn.example/live.flv\nSECRET_TOKEN_VALUE"
                                },
                                "hls_pull_url_map": {
                                    "FULL_HD1": "https://cdn.example/live.m3u8\r\nSECRET_COOKIE_VALUE"
                                },
                            },
                        }
                    ]
                }
            },
        )
    )

    with pytest.raises(StreamResolveError, match="stream URLs") as exc_info:
        await source.resolve("335354047186", AuthMode.ANONYMOUS)

    message = str(exc_info.value)
    assert "SECRET_TOKEN_VALUE" not in message
    assert "SECRET_COOKIE_VALUE" not in message
    assert "cdn.example" not in message
    await client.aclose()


class RecordingFrameReader:
    def __init__(self) -> None:
        self.streams: list[StreamInfo] = []

    async def frames(self, stream_info: StreamInfo) -> AsyncIterator[FramePacket]:
        self.streams.append(stream_info)
        yield FramePacket(
            data=np.zeros((2, 2, 3), dtype=np.uint8),
            received_at=1.0,
            width=2,
            height=2,
            sequence=0,
        )


@pytest.mark.asyncio
async def test_douyin_frames_delegate_to_configured_frame_reader() -> None:
    reader = RecordingFrameReader()
    source = DouyinStreamSource(frame_reader=reader)
    stream = StreamInfo(
        platform="douyin",
        room_id="335354047186",
        url="https://cdn.example/live.flv",
        format=StreamFormat.FLV,
        auth_mode=AuthMode.ANONYMOUS,
        ttl_seconds=60,
        requires_cookie=False,
    )

    frames = [frame async for frame in source.frames(stream)]

    assert reader.streams == [stream]
    assert len(frames) == 1
    assert frames[0].sequence == 0
