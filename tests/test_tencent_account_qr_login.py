from pathlib import Path

import httpx
import pytest

from qr_live_scanner_tencent.accounts import FakeAccountStore
from qr_live_scanner_tencent.accounts.device import LocalDeviceIdStore
from qr_live_scanner_tencent.accounts.tencent_qr_login import (
    TencentAccountQRLoginError,
    TencentAccountQRLoginService,
    TencentAccountQRLoginState,
)
from qr_live_scanner_tencent.interfaces import TencentLoginProvider


def test_tencent_account_qr_login_default_configs_are_gated() -> None:
    configs = TencentAccountQRLoginService.default_configs()

    assert set(configs) == {TencentLoginProvider.QQ, TencentLoginProvider.WECHAT}
    assert configs[TencentLoginProvider.QQ].validated_protocol is False
    assert configs[TencentLoginProvider.WECHAT].validated_protocol is False


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
