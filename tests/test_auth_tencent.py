import time

import httpx
import pytest

from qr_live_scanner_tencent.accounts import FakeAccountStore, TencentSession
from qr_live_scanner_tencent.auth.tencent import (
    TencentGameAuthAdapter,
    TencentGameConfig,
    default_game_configs,
    parse_tencent_game_qr_payload,
)
from qr_live_scanner_tencent.interfaces import (
    AccountRef,
    AuthorizationError,
    GameID,
    QRCandidate,
    ROIConfig,
    TencentLoginProvider,
)


def _candidate(payload: str) -> QRCandidate:
    return QRCandidate(
        payload=payload,
        detected_at=time.perf_counter(),
        source_frame_received_at=time.perf_counter(),
        roi=ROIConfig.full_frame(),
        backend="test",
    )


def _config() -> TencentGameConfig:
    return TencentGameConfig(
        game_id=GameID.HONOR_OF_KINGS,
        name="王者荣耀",
        provider=TencentLoginProvider.QQ,
        scan_endpoint="https://example.test/tencent/scan",
        confirm_endpoint="https://example.test/tencent/confirm",
        validated_protocol=True,
    )


def _store(account: AccountRef) -> FakeAccountStore:
    store = FakeAccountStore()
    store.save_tencent_session(
        TencentSession(
            uid=account.uid,
            provider=account.provider,
            credentials={"access_token": "SECRET_ACCESS_TOKEN", "openid": "SECRET_OPENID"},
        ),
        authorized=True,
    )
    return store


def test_default_game_configs_only_cover_honor_of_kings_and_are_gated() -> None:
    configs = default_game_configs()

    assert set(configs) == {GameID.HONOR_OF_KINGS}
    assert configs[GameID.HONOR_OF_KINGS].name == "王者荣耀"
    assert configs[GameID.HONOR_OF_KINGS].provider is TencentLoginProvider.QQ
    assert configs[GameID.HONOR_OF_KINGS].validated_protocol is False


def test_parse_tencent_game_qr_payload_extracts_safe_fields() -> None:
    parsed = parse_tencent_game_qr_payload(
        "https://ssl.ptlogin2.qq.com/ptqrlogin?ptqrtoken=SECRET_TICKET&appid=hok"
    )

    assert parsed.provider is TencentLoginProvider.QQ
    assert parsed.ticket == "SECRET_TICKET"
    assert parsed.game_hint == "hok"
    assert "SECRET_TICKET" not in parsed.safe_description()


def test_parse_tencent_game_qr_payload_detects_wechat_before_shared_qq_domain() -> None:
    parsed = parse_tencent_game_qr_payload(
        "https://weixin.qq.com/game/login?ticket=SECRET_TICKET"
    )

    assert parsed.provider is TencentLoginProvider.WECHAT
    assert parsed.ticket == "SECRET_TICKET"
    assert "SECRET_TICKET" not in parsed.safe_description()


def test_parse_tencent_game_qr_payload_rejects_missing_ticket_without_echo() -> None:
    payload = "https://ssl.ptlogin2.qq.com/ptqrlogin?appid=hok"

    with pytest.raises(AuthorizationError, match="ticket") as exc_info:
        parse_tencent_game_qr_payload(payload)

    assert "appid=hok" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_tencent_adapter_scan_and_confirm_are_mockable() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/scan"):
            return httpx.Response(200, json={"scan_token": "SECRET_SCAN_TOKEN"})
        return httpx.Response(200, json={"success": True})

    account = AccountRef(
        uid="10001",
        game_id=GameID.HONOR_OF_KINGS,
        provider=TencentLoginProvider.QQ,
    )
    adapter = TencentGameAuthAdapter(
        config=_config(),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        account_store=_store(account),
    )

    scan = await adapter.scan(
        _candidate("https://ssl.ptlogin2.qq.com/ptqrlogin?ptqrtoken=SECRET_TICKET"),
        account,
    )
    confirm = await adapter.confirm(scan)

    assert scan.scan_token == "SECRET_SCAN_TOKEN"
    assert confirm.success is True
    assert len(requests) == 2
    assert requests[0].headers["cookie"]
    assert "SECRET_TICKET" not in confirm.message


@pytest.mark.asyncio
async def test_tencent_adapter_rejects_unvalidated_default_config_before_http() -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"scan_token": "x"})

    account = AccountRef(uid="10001", game_id=GameID.HONOR_OF_KINGS)
    adapter = TencentGameAuthAdapter(
        config=default_game_configs()[GameID.HONOR_OF_KINGS],
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(ValueError, match="not validated"):
        await adapter.scan(
            _candidate("https://ssl.ptlogin2.qq.com/ptqrlogin?ptqrtoken=SECRET_TICKET"),
            account,
        )

    assert called is False


@pytest.mark.asyncio
async def test_tencent_adapter_requires_session_before_validated_http() -> None:
    account = AccountRef(uid="10001", game_id=GameID.HONOR_OF_KINGS)
    adapter = TencentGameAuthAdapter(
        config=_config(),
        client=httpx.AsyncClient(),
        account_store=FakeAccountStore(),
    )

    with pytest.raises(AuthorizationError, match="Tencent session") as exc_info:
        await adapter.scan(
            _candidate("https://ssl.ptlogin2.qq.com/ptqrlogin?ptqrtoken=SECRET_TICKET"),
            account,
        )

    assert "SECRET_TICKET" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_tencent_adapter_rejects_account_provider_mismatch_before_http() -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"scan_token": "SECRET_SCAN_TOKEN"})

    account = AccountRef(
        uid="wechat-user",
        game_id=GameID.HONOR_OF_KINGS,
        provider=TencentLoginProvider.WECHAT,
    )
    adapter = TencentGameAuthAdapter(
        config=_config(),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        account_store=_store(account),
    )

    with pytest.raises(AuthorizationError, match="provider") as exc_info:
        await adapter.scan(
            _candidate(
                "https://login.example.test/game/login?provider=wechat&ticket=SECRET_TICKET"
            ),
            account,
        )

    assert called is False
    assert "SECRET_TICKET" not in str(exc_info.value)
