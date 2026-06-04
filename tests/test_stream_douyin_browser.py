from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from qr_live_scanner_tencent.interfaces import AuthMode, StreamFormat, StreamResolveError
from qr_live_scanner_tencent.stream import douyin_browser
from qr_live_scanner_tencent.stream.douyin_browser import (
    BrowserResponse,
    DouyinBrowserStreamSource,
    PlaywrightDouyinBrowserResolver,
    extract_web_rid,
    is_douyin_enter_url,
    parse_douyin_enter_payload,
)


@dataclass(slots=True)
class FakeBrowserResolver:
    response: BrowserResponse | None = None
    error: Exception | None = None
    calls: list[str] | None = None

    async def capture_enter_response(self, room_url: str) -> BrowserResponse:
        if self.calls is None:
            self.calls = []
        self.calls.append(room_url)
        if self.error is not None:
            raise self.error
        if self.response is None:
            raise AssertionError("fake response was not configured")
        return self.response


class FakePlaywrightResponse:
    def __init__(self, url: str, payload: dict[str, Any]) -> None:
        self.url = url
        self._payload = payload

    async def json(self) -> dict[str, Any]:
        return self._payload


class FakePage:
    def __init__(self, response: FakePlaywrightResponse) -> None:
        self.response = response
        self.visited_urls: list[str] = []

    async def goto(self, room_url: str, wait_until: str, timeout: int) -> None:
        self.visited_urls.append(room_url)

    async def wait_for_event(self, event_name: str, predicate: object, timeout: int) -> object:
        assert event_name == "response"
        assert callable(predicate)
        assert predicate(self.response) is True
        return self.response


class FakeContext:
    def __init__(self, response: FakePlaywrightResponse) -> None:
        self.page = FakePage(response)
        self.closed = False

    @property
    def pages(self) -> list[FakePage]:
        return [self.page]

    async def close(self) -> None:
        self.closed = True


class FakeChromium:
    def __init__(self, context: FakeContext) -> None:
        self.context = context
        self.launch_kwargs: dict[str, Any] | None = None

    async def launch_persistent_context(self, **kwargs: Any) -> FakeContext:
        self.launch_kwargs = kwargs
        return self.context


class FakePlaywright:
    def __init__(self, chromium: FakeChromium) -> None:
        self.chromium = chromium


class FakeAsyncPlaywrightManager:
    def __init__(self, playwright: FakePlaywright) -> None:
        self.playwright = playwright

    async def __aenter__(self) -> FakePlaywright:
        return self.playwright

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


def enter_payload(*, flv: str | None = "https://cdn.example/live.flv") -> dict[str, Any]:
    stream_url: dict[str, Any] = {
        "flv_pull_url": {"FULL_HD1": flv} if flv else {},
        "hls_pull_url_map": {"FULL_HD1": "https://cdn.example/live.m3u8"},
    }
    return {"data": {"data": [{"status": 2, "stream_url": stream_url}]}}


def test_douyin_enter_url_matcher_accepts_only_enter_endpoint() -> None:
    assert (
        is_douyin_enter_url(
            "https://live.douyin.com/webcast/room/web/enter/?web_rid=123&a_bogus=SECRET"
        )
        is True
    )
    assert is_douyin_enter_url("https://live.douyin.com/webcast/room/info/?web_rid=123") is False
    assert is_douyin_enter_url("https://evil.example/webcast/room/web/enter/") is False


def test_extract_web_rid_accepts_room_url_or_plain_id_without_echoing_secrets() -> None:
    assert extract_web_rid("https://live.douyin.com/335354047186?foo=bar") == "335354047186"
    assert extract_web_rid("335354047186") == "335354047186"

    with pytest.raises(StreamResolveError, match="room id") as exc_info:
        extract_web_rid("https://live.douyin.com/335354047186\ncookie=SECRET")

    assert "SECRET" not in str(exc_info.value)
    assert "cookie" not in str(exc_info.value).lower()


def test_parse_douyin_enter_payload_prefers_flv_and_falls_back_to_hls() -> None:
    stream_url, stream_format = parse_douyin_enter_payload(enter_payload())

    assert stream_url == "https://cdn.example/live.flv"
    assert stream_format is StreamFormat.FLV

    stream_url, stream_format = parse_douyin_enter_payload(enter_payload(flv=None))

    assert stream_url == "https://cdn.example/live.m3u8"
    assert stream_format is StreamFormat.HLS


def test_parse_douyin_enter_payload_errors_are_sanitized() -> None:
    with pytest.raises(StreamResolveError, match="malformed room status") as exc_info:
        parse_douyin_enter_payload(
            {"data": {"data": [{"status": "SECRET_COOKIE_VALUE", "stream_url": {}}]}}
        )

    assert "SECRET_COOKIE_VALUE" not in str(exc_info.value)
    assert "cookie" not in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_browser_source_resolves_stream_info_from_fake_browser_response() -> None:
    browser = FakeBrowserResolver(
        response=BrowserResponse(
            url="https://live.douyin.com/webcast/room/web/enter/?a_bogus=SECRET_TOKEN_VALUE",
            payload=enter_payload(),
        )
    )
    source = DouyinBrowserStreamSource(browser_resolver=browser)

    info = await source.resolve("https://live.douyin.com/335354047186", AuthMode.ANONYMOUS)

    assert browser.calls == ["https://live.douyin.com/335354047186"]
    assert info.platform == "douyin"
    assert info.room_id == "335354047186"
    assert info.url == "https://cdn.example/live.flv"
    assert info.format is StreamFormat.FLV
    assert info.requires_cookie is False
    assert info.headers is not None
    assert info.headers["Referer"] == "https://live.douyin.com/"


@pytest.mark.asyncio
async def test_browser_source_rejects_mismatched_browser_response_without_signed_query() -> None:
    browser = FakeBrowserResolver(
        response=BrowserResponse(
            url="https://live.douyin.com/webcast/room/info/?a_bogus=SECRET_TOKEN_VALUE",
            payload=enter_payload(),
        )
    )
    source = DouyinBrowserStreamSource(browser_resolver=browser)

    with pytest.raises(StreamResolveError, match="enter response") as exc_info:
        await source.resolve("335354047186", AuthMode.ANONYMOUS)

    message = str(exc_info.value)
    assert "SECRET_TOKEN_VALUE" not in message
    assert "a_bogus" not in message


@pytest.mark.asyncio
async def test_browser_source_wraps_browser_errors_without_room_url() -> None:
    browser = FakeBrowserResolver(error=RuntimeError("SECRET_ROOM_URL failed"))
    source = DouyinBrowserStreamSource(browser_resolver=browser)

    with pytest.raises(StreamResolveError, match="browser capture failed") as exc_info:
        await source.resolve("https://live.douyin.com/335354047186", AuthMode.ANONYMOUS)

    assert "SECRET_ROOM_URL" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_playwright_resolver_launches_chrome_profile_and_captures_enter_response(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    response = FakePlaywrightResponse(
        "https://live.douyin.com/webcast/room/web/enter/?a_bogus=SECRET_TOKEN_VALUE",
        enter_payload(),
    )
    context = FakeContext(response)
    chromium = FakeChromium(context)

    monkeypatch.setattr(
        douyin_browser,
        "async_playwright",
        lambda: FakeAsyncPlaywrightManager(FakePlaywright(chromium)),
    )

    resolver = PlaywrightDouyinBrowserResolver(
        user_data_dir=tmp_path / "douyin-profile",
        chrome_executable_path=tmp_path / "chrome.exe",
        timeout_seconds=1.5,
        headless=False,
    )

    captured = await resolver.capture_enter_response("https://live.douyin.com/335354047186")

    assert captured.payload == enter_payload()
    assert captured.url == response.url
    assert context.page.visited_urls == ["https://live.douyin.com/335354047186"]
    assert context.closed is True
    assert chromium.launch_kwargs is not None
    assert chromium.launch_kwargs["headless"] is False
    assert chromium.launch_kwargs["user_data_dir"] == str(tmp_path / "douyin-profile")
    assert chromium.launch_kwargs["executable_path"] == str(tmp_path / "chrome.exe")


@pytest.mark.asyncio
async def test_playwright_resolver_uses_bundled_chromium_when_path_is_not_overridden(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    response = FakePlaywrightResponse(
        "https://live.douyin.com/webcast/room/web/enter/",
        enter_payload(),
    )
    context = FakeContext(response)
    chromium = FakeChromium(context)

    monkeypatch.setattr(
        douyin_browser,
        "async_playwright",
        lambda: FakeAsyncPlaywrightManager(FakePlaywright(chromium)),
    )

    resolver = PlaywrightDouyinBrowserResolver(user_data_dir=tmp_path / "douyin-profile")

    await resolver.capture_enter_response("https://live.douyin.com/335354047186")

    assert chromium.launch_kwargs is not None
    assert "executable_path" not in chromium.launch_kwargs


@pytest.mark.asyncio
async def test_playwright_resolver_wraps_json_errors_without_response_body(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class BadResponse(FakePlaywrightResponse):
        async def json(self) -> dict[str, Any]:
            raise ValueError("SECRET_RESPONSE_BODY")

    context = FakeContext(
        BadResponse(
            "https://live.douyin.com/webcast/room/web/enter/?a_bogus=SECRET_TOKEN_VALUE",
            enter_payload(),
        )
    )
    chromium = FakeChromium(context)
    monkeypatch.setattr(
        douyin_browser,
        "async_playwright",
        lambda: FakeAsyncPlaywrightManager(FakePlaywright(chromium)),
    )
    resolver = PlaywrightDouyinBrowserResolver(user_data_dir=tmp_path)

    with pytest.raises(StreamResolveError, match="JSON") as exc_info:
        await resolver.capture_enter_response("https://live.douyin.com/335354047186")

    message = str(exc_info.value)
    assert "SECRET_RESPONSE_BODY" not in message
    assert "SECRET_TOKEN_VALUE" not in message
