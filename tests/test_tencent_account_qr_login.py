from pathlib import Path

import httpx
import pytest

from qr_live_scanner_tencent.accounts import FakeAccountStore
from qr_live_scanner_tencent.accounts.device import LocalDeviceIdStore
from qr_live_scanner_tencent.accounts.tencent_qr_login import (
    TencentAccountQRLoginError,
    TencentAccountQRLoginProtocolMode,
    TencentAccountQRLoginService,
    TencentAccountQRLoginState,
    load_tencent_account_qr_login_config,
)
from qr_live_scanner_tencent.interfaces import TencentLoginProvider


def test_tencent_account_qr_login_default_configs_are_gated() -> None:
    configs = TencentAccountQRLoginService.default_configs()

    assert set(configs) == {TencentLoginProvider.QQ, TencentLoginProvider.WECHAT}
    assert configs[TencentLoginProvider.QQ].validated_protocol is False
    assert configs[TencentLoginProvider.WECHAT].validated_protocol is False


def test_load_tencent_account_qr_login_config_validates_provider_section(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "tencent-account-login.toml"
    config_path.write_text(
        "\n".join(
            [
                "[account_qr_login.qq]",
                "validated_protocol = true",
                'fetch_url = "https://example.test/qq/fetch"',
                'query_url = "https://example.test/qq/query"',
                'app_id = "test-app"',
            ]
        ),
        encoding="utf-8",
    )

    config = load_tencent_account_qr_login_config(config_path, TencentLoginProvider.QQ)

    assert config.provider is TencentLoginProvider.QQ
    assert config.validated_protocol is True
    assert config.fetch_url == "https://example.test/qq/fetch"
    assert config.query_url == "https://example.test/qq/query"
    assert config.app_id == "test-app"
    assert config.protocol_mode is TencentAccountQRLoginProtocolMode.JSON_POST


def test_load_tencent_account_qr_login_config_accepts_qq_ptlogin_mode(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "tencent-account-login.toml"
    config_path.write_text(
        "\n".join(
            [
                "[account_qr_login.qq]",
                "validated_protocol = true",
                'protocol_mode = "qq_ptlogin"',
                'fetch_url = "https://ssl.ptlogin2.qq.com/ptqrshow"',
                'query_url = "https://ssl.ptlogin2.qq.com/ptqrlogin"',
                'app_id = "test-app"',
            ]
        ),
        encoding="utf-8",
    )

    config = load_tencent_account_qr_login_config(config_path, TencentLoginProvider.QQ)

    assert config.protocol_mode is TencentAccountQRLoginProtocolMode.QQ_PTLOGIN


def test_load_tencent_account_qr_login_config_accepts_qq_qrconnect_mode(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "tencent-account-login.toml"
    config_path.write_text(
        "\n".join(
            [
                "[account_qr_login.qq]",
                "validated_protocol = true",
                'protocol_mode = "qq_qrconnect"',
                'fetch_url = "https://graph.qq.com/oauth2.0/authorize"',
                'query_url = "https://graph.qq.com/oauth2.0/token"',
                'redirect_uri = "https://login.example.test/oauth/qq/callback"',
                'callback_bind_url = "http://127.0.0.1:8765/qq/callback"',
                'app_id = "qq-app"',
            ]
        ),
        encoding="utf-8",
    )

    config = load_tencent_account_qr_login_config(config_path, TencentLoginProvider.QQ)

    assert config.protocol_mode is TencentAccountQRLoginProtocolMode.QQ_QRCONNECT
    assert config.redirect_uri == "https://login.example.test/oauth/qq/callback"
    assert config.callback_bind_url == "http://127.0.0.1:8765/qq/callback"


def test_load_tencent_account_qr_login_config_accepts_wechat_qrconnect_mode(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "tencent-account-login.toml"
    config_path.write_text(
        "\n".join(
            [
                "[account_qr_login.wechat]",
                "validated_protocol = true",
                'protocol_mode = "wechat_qrconnect"',
                'fetch_url = "https://open.weixin.qq.com/connect/qrconnect"',
                'query_url = "https://api.weixin.qq.com/sns/oauth2/access_token"',
                'redirect_uri = "https://login.example.test/oauth/wechat/callback"',
                'callback_bind_url = "http://127.0.0.1:8766/wechat/callback"',
                'app_id = "wechat-app"',
            ]
        ),
        encoding="utf-8",
    )

    config = load_tencent_account_qr_login_config(config_path, TencentLoginProvider.WECHAT)

    assert config.protocol_mode is TencentAccountQRLoginProtocolMode.WECHAT_QRCONNECT
    assert config.redirect_uri == "https://login.example.test/oauth/wechat/callback"
    assert config.callback_bind_url == "http://127.0.0.1:8766/wechat/callback"


def test_load_tencent_account_qr_login_config_rejects_sensitive_fields_without_echo(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "tencent-account-login.toml"
    config_path.write_text(
        "\n".join(
            [
                "[account_qr_login.qq]",
                "validated_protocol = true",
                'fetch_url = "https://example.test/qq/fetch"',
                'query_url = "https://example.test/qq/query"',
                'app_id = "test-app"',
                'access_token = "SECRET_ACCESS_TOKEN"',
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(TencentAccountQRLoginError, match="sensitive") as exc_info:
        load_tencent_account_qr_login_config(config_path, TencentLoginProvider.QQ)

    assert "SECRET_ACCESS_TOKEN" not in str(exc_info.value)


def test_load_tencent_account_qr_login_config_rejects_signed_urls_without_echo(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "tencent-account-login.toml"
    config_path.write_text(
        "\n".join(
            [
                "[account_qr_login.qq]",
                "validated_protocol = true",
                'fetch_url = "https://example.test/qq/fetch?ticket=SECRET_TICKET"',
                'query_url = "https://example.test/qq/query"',
                'app_id = "test-app"',
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(TencentAccountQRLoginError, match="endpoint") as exc_info:
        load_tencent_account_qr_login_config(config_path, TencentLoginProvider.QQ)

    assert "SECRET_TICKET" not in str(exc_info.value)


def test_load_tencent_account_qr_login_config_rejects_sensitive_path_segments_without_echo(
    tmp_path: Path,
) -> None:
    secret = "SECRET_TICKET_VALUE_DO_NOT_LEAK"
    config_path = tmp_path / "tencent-account-login.toml"
    config_path.write_text(
        "\n".join(
            [
                "[account_qr_login.qq]",
                "validated_protocol = true",
                f'fetch_url = "https://example.test/qq/fetch/ticket/{secret}"',
                'query_url = "https://example.test/qq/query"',
                'app_id = "test-app"',
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(TencentAccountQRLoginError, match="endpoint") as exc_info:
        load_tencent_account_qr_login_config(config_path, TencentLoginProvider.QQ)

    assert secret not in str(exc_info.value)


def test_load_tencent_account_qr_login_config_rejects_sensitive_app_id_without_echo(
    tmp_path: Path,
) -> None:
    secret = "SECRET_TOKEN_VALUE_DO_NOT_LEAK"
    config_path = tmp_path / "tencent-account-login.toml"
    config_path.write_text(
        "\n".join(
            [
                "[account_qr_login.qq]",
                "validated_protocol = true",
                'fetch_url = "https://example.test/qq/fetch"',
                'query_url = "https://example.test/qq/query"',
                f'app_id = "{secret}"',
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(TencentAccountQRLoginError, match="app id") as exc_info:
        load_tencent_account_qr_login_config(config_path, TencentLoginProvider.QQ)

    assert secret not in str(exc_info.value)


def test_load_tencent_account_qr_login_config_rejects_public_callback_bind_url(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "tencent-account-login.toml"
    config_path.write_text(
        "\n".join(
            [
                "[account_qr_login.qq]",
                "validated_protocol = true",
                'protocol_mode = "qq_qrconnect"',
                'fetch_url = "https://graph.qq.com/oauth2.0/authorize"',
                'query_url = "https://graph.qq.com/oauth2.0/token"',
                'redirect_uri = "https://login.example.test/qq/callback"',
                'callback_bind_url = "http://login.example.test/qq/callback"',
                'app_id = "qq-app"',
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(TencentAccountQRLoginError, match="local") as exc_info:
        load_tencent_account_qr_login_config(config_path, TencentLoginProvider.QQ)

    assert "login.example.test" not in str(exc_info.value)


def test_load_tencent_account_qr_login_config_requires_bind_for_public_redirect(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "tencent-account-login.toml"
    config_path.write_text(
        "\n".join(
            [
                "[account_qr_login.wechat]",
                "validated_protocol = true",
                'protocol_mode = "wechat_qrconnect"',
                'fetch_url = "https://open.weixin.qq.com/connect/qrconnect"',
                'query_url = "https://api.weixin.qq.com/sns/oauth2/access_token"',
                'redirect_uri = "https://login.example.test/wechat/callback"',
                'app_id = "wechat-app"',
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(TencentAccountQRLoginError, match="callback bind") as exc_info:
        load_tencent_account_qr_login_config(config_path, TencentLoginProvider.WECHAT)

    assert "login.example.test" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_tencent_account_qr_login_rejects_unvalidated_config_before_http() -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    service = TencentAccountQRLoginService(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        device_id_store=LocalDeviceIdStore.fixed("0123456789abcdef0123456789abcdef"),
        config=TencentAccountQRLoginService.default_configs()[TencentLoginProvider.QQ],
    )

    with pytest.raises(TencentAccountQRLoginError, match="not validated"):
        await service.fetch_qr()

    assert called is False
    await service.aclose()


@pytest.mark.asyncio
async def test_tencent_account_qr_login_fetches_ticket_without_exposing_payload() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "retcode": 0,
                "data": {
                    "qr_url": "https://example.test/qq/qr?ticket=SECRET_TICKET",
                    "ticket": "SECRET_TICKET",
                    "expires_in": 180,
                },
            },
        )

    config = TencentAccountQRLoginService.default_configs()[TencentLoginProvider.QQ].validated(
        fetch_url="https://example.test/qq/fetch",
        query_url="https://example.test/qq/query",
        app_id="test-app",
    )
    service = TencentAccountQRLoginService(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        device_id_store=LocalDeviceIdStore.fixed("0123456789abcdef0123456789abcdef"),
        config=config,
    )

    ticket = await service.fetch_qr()

    assert ticket.provider is TencentLoginProvider.QQ
    assert ticket.ticket == "SECRET_TICKET"
    assert ticket.qr_url == "https://example.test/qq/qr?ticket=SECRET_TICKET"
    assert ticket.expires_in_seconds == 180
    assert requests[0].method == "POST"
    assert requests[0].url.query == b""
    assert requests[0].read() == (
        b'{"app_id":"test-app","device":"0123456789abcdef0123456789abcdef",'
        b'"provider":"qq"}'
    )
    assert "SECRET_TICKET" not in ticket.safe_description()
    assert "example.test" not in ticket.safe_description()
    await service.aclose()


@pytest.mark.asyncio
async def test_tencent_account_qr_login_fetches_qq_ptlogin_image_ticket(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "qq-ptlogin.png"
    image_bytes = b"\x89PNG\r\n\x1a\nqq-ptlogin"
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"set-cookie": "qrsig=SECRET_QRSIG; Path=/; Secure; HttpOnly"},
            content=image_bytes,
        )

    config = TencentAccountQRLoginService.default_configs()[TencentLoginProvider.QQ].validated(
        fetch_url="https://ssl.ptlogin2.qq.com/ptqrshow",
        query_url="https://ssl.ptlogin2.qq.com/ptqrlogin",
        app_id="test-app",
        protocol_mode=TencentAccountQRLoginProtocolMode.QQ_PTLOGIN,
    )
    service = TencentAccountQRLoginService(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        device_id_store=LocalDeviceIdStore.fixed("0123456789abcdef0123456789abcdef"),
        config=config,
    )

    ticket = await service.fetch_qr()
    service.write_qr_png(ticket, output_path)

    assert ticket.ticket == "SECRET_QRSIG"
    assert ticket.qr_image_bytes == image_bytes
    assert output_path.read_bytes() == image_bytes
    assert requests[0].method == "GET"
    assert requests[0].url.params["appid"] == "test-app"
    assert "SECRET_QRSIG" not in ticket.safe_description()
    await service.aclose()


@pytest.mark.asyncio
async def test_tencent_account_qr_login_query_saves_confirmed_session() -> None:
    responses = [
        httpx.Response(
            200,
            json={
                "retcode": 0,
                "data": {
                    "qr_url": "https://example.test/qq/qr?ticket=SECRET_TICKET",
                    "ticket": "SECRET_TICKET",
                },
            },
        ),
        httpx.Response(
            200,
            json={
                "retcode": 0,
                "data": {
                    "state": "confirmed",
                    "uid": "10001",
                    "credentials": {
                        "access_token": "SECRET_ACCESS_TOKEN",
                        "openid": "SECRET_OPENID",
                    },
                },
            },
        ),
    ]

    def handler(_request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    store = FakeAccountStore()
    service = TencentAccountQRLoginService(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        device_id_store=LocalDeviceIdStore.fixed("0123456789abcdef0123456789abcdef"),
        config=TencentAccountQRLoginService.default_configs()[TencentLoginProvider.QQ].validated(
            fetch_url="https://example.test/qq/fetch",
            query_url="https://example.test/qq/query",
            app_id="test-app",
        ),
    )

    ticket = await service.fetch_qr()
    status = await service.query_qr(ticket)
    session = service.save_confirmed_session(status, store)

    assert status.state is TencentAccountQRLoginState.CONFIRMED
    assert session.uid == "10001"
    assert store.get_tencent_session("10001", TencentLoginProvider.QQ) == session
    assert store.is_tencent_authorized("10001", TencentLoginProvider.QQ) is True
    assert "SECRET_ACCESS_TOKEN" not in session.safe_description()
    assert "10001" not in session.safe_description()
    await service.aclose()


@pytest.mark.asyncio
async def test_tencent_account_qr_login_qq_ptlogin_query_builds_cookie_session() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers=[
                ("set-cookie", "uin=o10001; Path=/; Secure; HttpOnly"),
                ("set-cookie", "skey=SECRET_SKEY; Path=/; Secure; HttpOnly"),
            ],
            text="ptuiCB('0','0','https://ssl.ptlogin2.qq.com/check_sig','0','mock-nick');",
        )

    config = TencentAccountQRLoginService.default_configs()[TencentLoginProvider.QQ].validated(
        fetch_url="https://ssl.ptlogin2.qq.com/ptqrshow",
        query_url="https://ssl.ptlogin2.qq.com/ptqrlogin",
        app_id="test-app",
        protocol_mode=TencentAccountQRLoginProtocolMode.QQ_PTLOGIN,
    )
    service = TencentAccountQRLoginService(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        device_id_store=LocalDeviceIdStore.fixed("0123456789abcdef0123456789abcdef"),
        config=config,
    )

    status = await service.query_qr(
        service.ticket_from_values(
            ticket="SECRET_QRSIG",
            qr_url="https://ssl.ptlogin2.qq.com/ptqrshow",
        )
    )

    assert status.state is TencentAccountQRLoginState.CONFIRMED
    assert status.session is not None
    assert status.session.uid == "10001"
    assert status.session.credentials["cookie_uin"] == "o10001"
    assert status.session.credentials["cookie_skey"] == "SECRET_SKEY"
    assert requests[0].method == "GET"
    assert requests[0].url.params["aid"] == "test-app"
    assert requests[0].url.params["ptqrtoken"] == _qq_hash33("SECRET_QRSIG")
    assert "SECRET_SKEY" not in status.safe_description()
    await service.aclose()


@pytest.mark.asyncio
async def test_tencent_account_qr_login_qq_qrconnect_builds_authorize_qr_url() -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500)

    config = TencentAccountQRLoginService.default_configs()[TencentLoginProvider.QQ].validated(
        fetch_url="https://graph.qq.com/oauth2.0/authorize",
        query_url="https://graph.qq.com/oauth2.0/token",
        redirect_uri="https://login.example.test/qq/callback",
        app_id="qq-app",
        protocol_mode=TencentAccountQRLoginProtocolMode.QQ_QRCONNECT,
    )
    service = TencentAccountQRLoginService(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        device_id_store=LocalDeviceIdStore.fixed("0123456789abcdef0123456789abcdef"),
        config=config,
    )

    ticket = await service.fetch_qr()

    assert called is False
    assert ticket.provider is TencentLoginProvider.QQ
    assert ticket.ticket
    assert ticket.qr_url.startswith("https://graph.qq.com/oauth2.0/authorize?")
    assert "client_id=qq-app" in ticket.qr_url
    assert "response_type=code" in ticket.qr_url
    assert "scope=get_user_info" in ticket.qr_url
    assert "state=" in ticket.qr_url
    assert "SECRET" not in ticket.safe_description()
    await service.aclose()


@pytest.mark.asyncio
async def test_tencent_account_qr_login_qq_qrconnect_waits_for_callback_code() -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500)

    service = TencentAccountQRLoginService(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        device_id_store=LocalDeviceIdStore.fixed("0123456789abcdef0123456789abcdef"),
        config=TencentAccountQRLoginService.default_configs()[TencentLoginProvider.QQ].validated(
            fetch_url="https://graph.qq.com/oauth2.0/authorize",
            query_url="https://graph.qq.com/oauth2.0/token",
            redirect_uri="https://login.example.test/qq/callback",
            app_id="qq-app",
            protocol_mode=TencentAccountQRLoginProtocolMode.QQ_QRCONNECT,
        ),
    )
    ticket = await service.fetch_qr()

    status = await service.query_qr(ticket)

    assert status.state is TencentAccountQRLoginState.WAITING
    assert status.session is None
    assert called is False
    await service.aclose()


@pytest.mark.asyncio
async def test_tencent_account_qr_login_qq_qrconnect_exchanges_callback_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "access_token": "SECRET_QQ_ACCESS_TOKEN",
                "expires_in": 7776000,
                "refresh_token": "SECRET_QQ_REFRESH_TOKEN",
                "openid": "SECRET_QQ_OPENID",
                "scope": "get_user_info",
            },
        )

    monkeypatch.setenv("QR_LIVE_SCANNER_TENCENT_QQ_APP_SECRET", "SECRET_QQ_APP_SECRET")
    service = TencentAccountQRLoginService(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        device_id_store=LocalDeviceIdStore.fixed("0123456789abcdef0123456789abcdef"),
        config=TencentAccountQRLoginService.default_configs()[TencentLoginProvider.QQ].validated(
            fetch_url="https://graph.qq.com/oauth2.0/authorize",
            query_url="https://graph.qq.com/oauth2.0/token",
            redirect_uri="https://login.example.test/qq/callback",
            app_id="qq-app",
            protocol_mode=TencentAccountQRLoginProtocolMode.QQ_QRCONNECT,
        ),
    )
    ticket = await service.fetch_qr()
    service.accept_oauth_callback(state=ticket.ticket, code="SECRET_QQ_CODE")

    status = await service.query_qr(ticket)

    assert status.state is TencentAccountQRLoginState.CONFIRMED
    assert status.session is not None
    assert status.session.uid == "SECRET_QQ_OPENID"
    assert status.session.provider is TencentLoginProvider.QQ
    assert status.session.credentials["access_token"] == "SECRET_QQ_ACCESS_TOKEN"
    assert status.session.credentials["openid"] == "SECRET_QQ_OPENID"
    assert requests[0].method == "GET"
    assert requests[0].url.params["client_id"] == "qq-app"
    assert requests[0].url.params["client_secret"] == "SECRET_QQ_APP_SECRET"
    assert requests[0].url.params["code"] == "SECRET_QQ_CODE"
    assert requests[0].url.params["grant_type"] == "authorization_code"
    assert requests[0].url.params["need_openid"] == "1"
    assert "SECRET_QQ_ACCESS_TOKEN" not in status.safe_description()
    assert "SECRET_QQ_OPENID" not in status.safe_description()
    await service.aclose()


@pytest.mark.asyncio
async def test_tencent_account_qr_login_qq_qrconnect_fetches_openid_when_needed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url).startswith("https://graph.qq.com/oauth2.0/me"):
            return httpx.Response(
                200,
                text='callback( {"client_id":"qq-app","openid":"SECRET_QQ_OPENID"} );',
            )
        return httpx.Response(
            200,
            text="access_token=SECRET_QQ_ACCESS_TOKEN&expires_in=7776000&refresh_token=SECRET_QQ_REFRESH_TOKEN",
        )

    monkeypatch.setenv("QR_LIVE_SCANNER_TENCENT_QQ_APP_SECRET", "SECRET_QQ_APP_SECRET")
    service = TencentAccountQRLoginService(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        device_id_store=LocalDeviceIdStore.fixed("0123456789abcdef0123456789abcdef"),
        config=TencentAccountQRLoginService.default_configs()[TencentLoginProvider.QQ].validated(
            fetch_url="https://graph.qq.com/oauth2.0/authorize",
            query_url="https://graph.qq.com/oauth2.0/token",
            redirect_uri="https://login.example.test/qq/callback",
            app_id="qq-app",
            protocol_mode=TencentAccountQRLoginProtocolMode.QQ_QRCONNECT,
        ),
    )
    ticket = await service.fetch_qr()
    service.accept_oauth_callback(state=ticket.ticket, code="SECRET_QQ_CODE")

    status = await service.query_qr(ticket)

    assert status.state is TencentAccountQRLoginState.CONFIRMED
    assert status.session is not None
    assert status.session.uid == "SECRET_QQ_OPENID"
    assert len(requests) == 2
    assert requests[1].url.params["access_token"] == "SECRET_QQ_ACCESS_TOKEN"
    assert "SECRET_QQ_ACCESS_TOKEN" not in status.safe_description()
    await service.aclose()


@pytest.mark.asyncio
async def test_tencent_account_qr_login_qq_qrconnect_local_callback_completes_login(
    monkeypatch: pytest.MonkeyPatch,
    unused_tcp_port: int,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "access_token": "SECRET_QQ_ACCESS_TOKEN",
                "openid": "SECRET_QQ_OPENID",
            },
        )

    redirect_uri = f"http://127.0.0.1:{unused_tcp_port}/qq/callback"
    monkeypatch.setenv("QR_LIVE_SCANNER_TENCENT_QQ_APP_SECRET", "SECRET_QQ_APP_SECRET")
    service = TencentAccountQRLoginService(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        device_id_store=LocalDeviceIdStore.fixed("0123456789abcdef0123456789abcdef"),
        config=TencentAccountQRLoginService.default_configs()[TencentLoginProvider.QQ].validated(
            fetch_url="https://graph.qq.com/oauth2.0/authorize",
            query_url="https://graph.qq.com/oauth2.0/token",
            redirect_uri=redirect_uri,
            app_id="qq-app",
            protocol_mode=TencentAccountQRLoginProtocolMode.QQ_QRCONNECT,
        ),
    )

    ticket = await service.fetch_qr()
    async with httpx.AsyncClient(trust_env=False) as browser:
        callback_response = await browser.get(
            f"{redirect_uri}?code=SECRET_QQ_CODE&state={ticket.ticket}"
        )
    status = await service.query_qr(ticket)

    assert callback_response.status_code == 200
    assert status.state is TencentAccountQRLoginState.CONFIRMED
    assert status.session is not None
    assert status.session.uid == "SECRET_QQ_OPENID"
    assert requests[0].url.params["code"] == "SECRET_QQ_CODE"
    assert "SECRET_QQ_CODE" not in callback_response.text
    await service.aclose()


@pytest.mark.asyncio
async def test_tencent_account_qr_login_qq_qrconnect_tunnel_callback_bind_url(
    monkeypatch: pytest.MonkeyPatch,
    unused_tcp_port: int,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "access_token": "SECRET_QQ_ACCESS_TOKEN",
                "openid": "SECRET_QQ_OPENID",
            },
        )

    public_redirect_uri = "https://login.example.test/oauth/qq/callback"
    callback_bind_url = f"http://127.0.0.1:{unused_tcp_port}/qq/callback"
    monkeypatch.setenv("QR_LIVE_SCANNER_TENCENT_QQ_APP_SECRET", "SECRET_QQ_APP_SECRET")
    service = TencentAccountQRLoginService(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        device_id_store=LocalDeviceIdStore.fixed("0123456789abcdef0123456789abcdef"),
        config=TencentAccountQRLoginService.default_configs()[TencentLoginProvider.QQ].validated(
            fetch_url="https://graph.qq.com/oauth2.0/authorize",
            query_url="https://graph.qq.com/oauth2.0/token",
            redirect_uri=public_redirect_uri,
            callback_bind_url=callback_bind_url,
            app_id="qq-app",
            protocol_mode=TencentAccountQRLoginProtocolMode.QQ_QRCONNECT,
        ),
    )

    ticket = await service.fetch_qr()
    async with httpx.AsyncClient(trust_env=False) as browser:
        callback_response = await browser.get(
            f"{callback_bind_url}?code=SECRET_QQ_CODE&state={ticket.ticket}"
        )
    status = await service.query_qr(ticket)

    assert callback_response.status_code == 200
    assert "redirect_uri=https%3A%2F%2Flogin.example.test%2Foauth%2Fqq%2Fcallback" in ticket.qr_url
    assert status.state is TencentAccountQRLoginState.CONFIRMED
    assert status.session is not None
    assert status.session.uid == "SECRET_QQ_OPENID"
    assert requests[0].url.params["redirect_uri"] == public_redirect_uri
    assert "SECRET_QQ_CODE" not in callback_response.text
    await service.aclose()


@pytest.mark.asyncio
async def test_tencent_account_qr_login_wechat_qrconnect_builds_authorize_qr_url() -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500)

    config = TencentAccountQRLoginService.default_configs()[
        TencentLoginProvider.WECHAT
    ].validated(
        fetch_url="https://open.weixin.qq.com/connect/qrconnect",
        query_url="https://api.weixin.qq.com/sns/oauth2/access_token",
        redirect_uri="https://login.example.test/wechat/callback",
        app_id="wechat-app",
        protocol_mode=TencentAccountQRLoginProtocolMode.WECHAT_QRCONNECT,
    )
    service = TencentAccountQRLoginService(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        device_id_store=LocalDeviceIdStore.fixed("0123456789abcdef0123456789abcdef"),
        config=config,
    )

    ticket = await service.fetch_qr()

    assert called is False
    assert ticket.provider is TencentLoginProvider.WECHAT
    assert ticket.ticket
    assert ticket.qr_url.startswith("https://open.weixin.qq.com/connect/qrconnect?")
    assert ticket.qr_url.endswith("#wechat_redirect")
    assert "appid=wechat-app" in ticket.qr_url
    assert "response_type=code" in ticket.qr_url
    assert "scope=snsapi_login" in ticket.qr_url
    assert "state=" in ticket.qr_url
    assert "SECRET" not in ticket.safe_description()
    await service.aclose()


@pytest.mark.asyncio
async def test_tencent_account_qr_login_wechat_qrconnect_waits_for_callback_code() -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500)

    service = TencentAccountQRLoginService(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        device_id_store=LocalDeviceIdStore.fixed("0123456789abcdef0123456789abcdef"),
        config=TencentAccountQRLoginService.default_configs()[
            TencentLoginProvider.WECHAT
        ].validated(
            fetch_url="https://open.weixin.qq.com/connect/qrconnect",
            query_url="https://api.weixin.qq.com/sns/oauth2/access_token",
            redirect_uri="https://login.example.test/wechat/callback",
            app_id="wechat-app",
            protocol_mode=TencentAccountQRLoginProtocolMode.WECHAT_QRCONNECT,
        ),
    )
    ticket = await service.fetch_qr()

    status = await service.query_qr(ticket)

    assert status.state is TencentAccountQRLoginState.WAITING
    assert status.session is None
    assert called is False
    await service.aclose()


@pytest.mark.asyncio
async def test_tencent_account_qr_login_wechat_qrconnect_exchanges_callback_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "access_token": "SECRET_WECHAT_ACCESS_TOKEN",
                "expires_in": 7200,
                "refresh_token": "SECRET_WECHAT_REFRESH_TOKEN",
                "openid": "SECRET_WECHAT_OPENID",
                "scope": "snsapi_login",
                "unionid": "SECRET_WECHAT_UNIONID",
            },
        )

    monkeypatch.setenv("QR_LIVE_SCANNER_TENCENT_WECHAT_APP_SECRET", "SECRET_APP_SECRET")
    service = TencentAccountQRLoginService(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        device_id_store=LocalDeviceIdStore.fixed("0123456789abcdef0123456789abcdef"),
        config=TencentAccountQRLoginService.default_configs()[
            TencentLoginProvider.WECHAT
        ].validated(
            fetch_url="https://open.weixin.qq.com/connect/qrconnect",
            query_url="https://api.weixin.qq.com/sns/oauth2/access_token",
            redirect_uri="https://login.example.test/wechat/callback",
            app_id="wechat-app",
            protocol_mode=TencentAccountQRLoginProtocolMode.WECHAT_QRCONNECT,
        ),
    )
    ticket = await service.fetch_qr()
    service.accept_oauth_callback(state=ticket.ticket, code="SECRET_WECHAT_CODE")

    status = await service.query_qr(ticket)

    assert status.state is TencentAccountQRLoginState.CONFIRMED
    assert status.session is not None
    assert status.session.uid == "SECRET_WECHAT_UNIONID"
    assert status.session.provider is TencentLoginProvider.WECHAT
    assert status.session.credentials["access_token"] == "SECRET_WECHAT_ACCESS_TOKEN"
    assert status.session.credentials["openid"] == "SECRET_WECHAT_OPENID"
    assert requests[0].method == "GET"
    assert requests[0].url.params["appid"] == "wechat-app"
    assert requests[0].url.params["secret"] == "SECRET_APP_SECRET"
    assert requests[0].url.params["code"] == "SECRET_WECHAT_CODE"
    assert requests[0].url.params["grant_type"] == "authorization_code"
    assert "SECRET_WECHAT_ACCESS_TOKEN" not in status.safe_description()
    assert "SECRET_WECHAT_UNIONID" not in status.safe_description()
    await service.aclose()


@pytest.mark.asyncio
async def test_tencent_account_qr_login_wechat_qrconnect_local_callback_completes_login(
    monkeypatch: pytest.MonkeyPatch,
    unused_tcp_port: int,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "access_token": "SECRET_WECHAT_ACCESS_TOKEN",
                "openid": "SECRET_WECHAT_OPENID",
            },
        )

    redirect_uri = f"http://127.0.0.1:{unused_tcp_port}/wechat/callback"
    monkeypatch.setenv("QR_LIVE_SCANNER_TENCENT_WECHAT_APP_SECRET", "SECRET_APP_SECRET")
    service = TencentAccountQRLoginService(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        device_id_store=LocalDeviceIdStore.fixed("0123456789abcdef0123456789abcdef"),
        config=TencentAccountQRLoginService.default_configs()[
            TencentLoginProvider.WECHAT
        ].validated(
            fetch_url="https://open.weixin.qq.com/connect/qrconnect",
            query_url="https://api.weixin.qq.com/sns/oauth2/access_token",
            redirect_uri=redirect_uri,
            app_id="wechat-app",
            protocol_mode=TencentAccountQRLoginProtocolMode.WECHAT_QRCONNECT,
        ),
    )

    ticket = await service.fetch_qr()
    async with httpx.AsyncClient(trust_env=False) as browser:
        callback_response = await browser.get(
            f"{redirect_uri}?code=SECRET_WECHAT_CODE&state={ticket.ticket}"
        )
    status = await service.query_qr(ticket)

    assert callback_response.status_code == 200
    assert status.state is TencentAccountQRLoginState.CONFIRMED
    assert status.session is not None
    assert status.session.uid == "SECRET_WECHAT_OPENID"
    assert requests[0].url.params["code"] == "SECRET_WECHAT_CODE"
    assert "SECRET_WECHAT_CODE" not in callback_response.text
    await service.aclose()


@pytest.mark.asyncio
async def test_tencent_account_qr_login_wechat_qrconnect_tunnel_callback_bind_url(
    monkeypatch: pytest.MonkeyPatch,
    unused_tcp_port: int,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "access_token": "SECRET_WECHAT_ACCESS_TOKEN",
                "openid": "SECRET_WECHAT_OPENID",
            },
        )

    public_redirect_uri = "https://login.example.test/oauth/wechat/callback"
    callback_bind_url = f"http://127.0.0.1:{unused_tcp_port}/wechat/callback"
    monkeypatch.setenv("QR_LIVE_SCANNER_TENCENT_WECHAT_APP_SECRET", "SECRET_APP_SECRET")
    service = TencentAccountQRLoginService(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        device_id_store=LocalDeviceIdStore.fixed("0123456789abcdef0123456789abcdef"),
        config=TencentAccountQRLoginService.default_configs()[
            TencentLoginProvider.WECHAT
        ].validated(
            fetch_url="https://open.weixin.qq.com/connect/qrconnect",
            query_url="https://api.weixin.qq.com/sns/oauth2/access_token",
            redirect_uri=public_redirect_uri,
            callback_bind_url=callback_bind_url,
            app_id="wechat-app",
            protocol_mode=TencentAccountQRLoginProtocolMode.WECHAT_QRCONNECT,
        ),
    )

    ticket = await service.fetch_qr()
    async with httpx.AsyncClient(trust_env=False) as browser:
        callback_response = await browser.get(
            f"{callback_bind_url}?code=SECRET_WECHAT_CODE&state={ticket.ticket}"
        )
    status = await service.query_qr(ticket)

    assert callback_response.status_code == 200
    assert (
        "redirect_uri=https%3A%2F%2Flogin.example.test%2Foauth%2Fwechat%2Fcallback"
        in ticket.qr_url
    )
    assert status.state is TencentAccountQRLoginState.CONFIRMED
    assert status.session is not None
    assert status.session.uid == "SECRET_WECHAT_OPENID"
    assert requests[0].url.params["appid"] == "wechat-app"
    assert "SECRET_WECHAT_CODE" not in callback_response.text
    await service.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw_state", "expected"),
    [
        ("waiting", TencentAccountQRLoginState.WAITING),
        ("scanned", TencentAccountQRLoginState.SCANNED),
        ("expired", TencentAccountQRLoginState.EXPIRED),
    ],
)
async def test_tencent_account_qr_login_maps_non_confirmed_states(
    raw_state: str,
    expected: TencentAccountQRLoginState,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"retcode": 0, "data": {"state": raw_state}})

    service = TencentAccountQRLoginService(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        device_id_store=LocalDeviceIdStore.fixed("0123456789abcdef0123456789abcdef"),
        config=TencentAccountQRLoginService.default_configs()[
            TencentLoginProvider.WECHAT
        ].validated(
            fetch_url="https://example.test/wechat/fetch",
            query_url="https://example.test/wechat/query",
            app_id="wechat-app",
        ),
    )

    status = await service.query_qr(
        service.ticket_from_values(
            ticket="SECRET_TICKET",
            qr_url="https://example.test/wechat/qr?ticket=SECRET_TICKET",
        )
    )

    assert status.state is expected
    assert status.session is None
    assert requests[0].method == "POST"
    assert requests[0].url.query == b""
    assert requests[0].read() == (
        b'{"app_id":"wechat-app","device":"0123456789abcdef0123456789abcdef",'
        b'"provider":"wechat","ticket":"SECRET_TICKET"}'
    )
    await service.aclose()


@pytest.mark.asyncio
async def test_tencent_account_qr_login_errors_do_not_expose_sensitive_response() -> None:
    service = TencentAccountQRLoginService(
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    json={
                        "retcode": 0,
                        "data": {
                            "state": "confirmed",
                            "uid": "10001",
                            "credentials": {"access_token": "SECRET_ACCESS_TOKEN"},
                        },
                    },
                )
            )
        ),
        device_id_store=LocalDeviceIdStore.fixed("0123456789abcdef0123456789abcdef"),
        config=TencentAccountQRLoginService.default_configs()[TencentLoginProvider.QQ].validated(
            fetch_url="https://example.test/qq/fetch",
            query_url="https://example.test/qq/query",
            app_id="test-app",
        ),
    )

    with pytest.raises(TencentAccountQRLoginError, match="confirmed session") as exc_info:
        await service.query_qr(
            service.ticket_from_values(
                ticket="SECRET_TICKET",
                qr_url="https://example.test/qq/qr?ticket=SECRET_TICKET",
            )
        )

    message = str(exc_info.value)
    assert "SECRET_ACCESS_TOKEN" not in message
    assert "SECRET_TICKET" not in message
    assert "10001" not in message
    await service.aclose()


def test_tencent_account_qr_login_dry_run_writes_safe_qr(tmp_path: Path) -> None:
    output_path = tmp_path / "tencent-login.png"
    service = TencentAccountQRLoginService.dry_run(
        provider=TencentLoginProvider.QQ,
        device_id_store=LocalDeviceIdStore.fixed("0123456789abcdef0123456789abcdef"),
    )

    ticket = service.dry_run_ticket()
    service.write_qr_png(ticket, output_path)

    assert ticket.provider is TencentLoginProvider.QQ
    assert output_path.exists()
    assert output_path.read_bytes().startswith(b"\x89PNG")
    assert "dry-run" in ticket.safe_description()
    assert ticket.ticket not in ticket.safe_description()


def _qq_hash33(value: str) -> str:
    token = 0
    for char in value:
        token += (token << 5) + ord(char)
    return str(token & 0x7FFFFFFF)
