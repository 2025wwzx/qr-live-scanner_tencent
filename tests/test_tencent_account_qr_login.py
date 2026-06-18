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
