from __future__ import annotations

import time
from dataclasses import dataclass
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlparse

import httpx

from qr_live_scanner_tencent.accounts import TencentSession
from qr_live_scanner_tencent.interfaces import (
    AccountRef,
    AccountStore,
    AuthorizationError,
    ConfirmResult,
    GameID,
    QRCandidate,
    ScanResult,
    TencentLoginProvider,
)


@dataclass(frozen=True, slots=True)
class TencentGameConfig:
    game_id: GameID
    name: str
    provider: TencentLoginProvider
    scan_endpoint: str
    confirm_endpoint: str
    qr_hosts: tuple[str, ...] = ()
    qr_path_keywords: tuple[str, ...] = ()
    validated_protocol: bool = False


@dataclass(frozen=True, slots=True)
class TencentGameQRPayload:
    provider: TencentLoginProvider | None
    ticket: str
    game_hint: str = ""

    def safe_description(self) -> str:
        provider = "unknown" if self.provider is None else self.provider.value
        return f"Tencent game QR provider={provider}"


def default_game_configs() -> dict[GameID, TencentGameConfig]:
    """返回腾讯版首轮锁定的游戏配置。

    默认配置仅用于路由和 mock 测试，不代表真实王者荣耀扫码协议已验证。
    `validated_protocol=False` 会在真实 HTTP 之前拦截 scan/confirm。
    """

    return {
        GameID.HONOR_OF_KINGS: TencentGameConfig(
            game_id=GameID.HONOR_OF_KINGS,
            name="王者荣耀",
            provider=TencentLoginProvider.QQ,
            scan_endpoint="https://example.invalid/tencent/honor-of-kings/scan",
            confirm_endpoint="https://example.invalid/tencent/honor-of-kings/confirm",
            qr_hosts=("ssl.ptlogin2.qq.com", "xui.ptlogin2.qq.com", "graph.qq.com"),
            qr_path_keywords=("qr", "login"),
        )
    }


@dataclass(slots=True)
class TencentGameAuthAdapter:
    config: TencentGameConfig
    client: httpx.AsyncClient
    account_store: AccountStore | None = None

    async def scan(self, candidate: QRCandidate, account: AccountRef) -> ScanResult:
        self._ensure_account_game(account)
        self._ensure_protocol_validated()
        qr_payload = _require_qr_payload(candidate.payload)
        parsed_payload = parse_tencent_game_qr_payload(qr_payload)
        self._ensure_qr_payload_matches_config(parsed_payload, account)
        try:
            response = await self.client.post(
                self.config.scan_endpoint,
                headers=self._session_headers(account),
                json={
                    "uid": account.uid,
                    "game_id": account.game_id.value,
                    "provider": account.provider.value,
                    "qr_payload": qr_payload,
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            msg = "Tencent scan HTTP failed"
            raise AuthorizationError(msg) from exc
        data = _response_json(response, "Tencent scan JSON failed")
        token = str(data.get("scan_token") or "")
        if not token:
            msg = "Tencent scan response did not include scan_token"
            raise AuthorizationError(msg)
        return ScanResult(candidate=candidate, account=account, scan_token=token)

    async def confirm(self, scan_result: ScanResult) -> ConfirmResult:
        self._ensure_account_game(scan_result.account)
        self._ensure_protocol_validated()
        scan_token = str(scan_result.scan_token).strip()
        if not scan_token:
            msg = "Tencent confirm scan token is required"
            raise AuthorizationError(msg)
        sent_at = time.perf_counter()
        try:
            response = await self.client.post(
                self.config.confirm_endpoint,
                headers=self._session_headers(scan_result.account),
                json={
                    "uid": scan_result.account.uid,
                    "game_id": scan_result.account.game_id.value,
                    "provider": scan_result.account.provider.value,
                    "scan_token": scan_token,
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            msg = "Tencent confirm HTTP failed"
            raise AuthorizationError(msg) from exc
        data = _response_json(response, "Tencent confirm JSON failed")
        success = bool(data.get("success"))
        return ConfirmResult(
            scan=scan_result,
            success=success,
            sent_at=sent_at,
            message="confirmed" if success else "not confirmed",
        )

    def _ensure_account_game(self, account: AccountRef) -> None:
        if account.game_id is not self.config.game_id:
            msg = (
                f"account game {account.game_id} does not match adapter game {self.config.game_id}"
            )
            raise ValueError(msg)

    def _ensure_protocol_validated(self) -> None:
        if not self.config.validated_protocol:
            msg = (
                f"Tencent protocol for {self.config.game_id.value} is not validated; "
                "inject a validated mock config in tests or confirm the real protocol first"
            )
            raise ValueError(msg)

    def _session_headers(self, account: AccountRef) -> dict[str, str]:
        if self.account_store is None:
            return {}
        session = self.account_store.get_tencent_session(account.uid, account.provider)
        if not isinstance(session, TencentSession):
            msg = "Tencent session is required before scan/confirm"
            raise AuthorizationError(msg)
        if session.provider is not account.provider:
            msg = "Tencent session provider does not match account provider"
            raise AuthorizationError(msg)
        return {"Cookie": _credential_cookie_header(session)}

    def _ensure_qr_payload_matches_config(
        self,
        payload: TencentGameQRPayload,
        account: AccountRef,
    ) -> None:
        if payload.provider is not None and payload.provider is not account.provider:
            msg = "Tencent QR payload provider does not match account provider"
            raise AuthorizationError(msg)


def parse_tencent_game_qr_payload(payload: str) -> TencentGameQRPayload:
    normalized = _require_qr_payload(payload)
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        msg = "Tencent game QR payload URL is not supported"
        raise AuthorizationError(msg)
    query = parse_qs(parsed.query, keep_blank_values=True)
    provider = _provider_from_payload(parsed.netloc, query)
    ticket = _first_query_value(query, ("ptqrtoken", "qrsig", "ticket", "token", "qr_token"))
    if not ticket:
        msg = "Tencent game QR payload missing ticket"
        raise AuthorizationError(msg)
    game_hint = _first_query_value(query, ("game", "game_id", "appid", "app_id")) or ""
    return TencentGameQRPayload(provider=provider, ticket=ticket, game_hint=game_hint)


def _response_json(response: httpx.Response, message: str) -> dict[str, object]:
    try:
        data = response.json()
    except ValueError as exc:
        raise AuthorizationError(message) from exc
    if not isinstance(data, dict):
        raise AuthorizationError(message)
    return data


def _require_qr_payload(payload: str) -> str:
    normalized = str(payload).strip()
    if not normalized:
        msg = "Tencent scan QR payload is required"
        raise AuthorizationError(msg)
    return normalized


def _credential_cookie_header(session: TencentSession) -> str:
    parts: list[str] = []
    for name in sorted(session.credentials):
        value = session.credentials[name]
        cookie = SimpleCookie()
        cookie[name] = value
        parts.append(f"{name}={cookie[name].coded_value}")
    return "; ".join(parts)


def _provider_from_payload(
    host: str,
    query: dict[str, list[str]],
) -> TencentLoginProvider | None:
    raw_provider = (_first_query_value(query, ("provider", "login_type", "channel")) or "").lower()
    if raw_provider in {"qq", "ptlogin"} or "qq.com" in host.lower():
        return TencentLoginProvider.QQ
    if raw_provider in {"wechat", "weixin", "wx"} or "weixin.qq.com" in host.lower():
        return TencentLoginProvider.WECHAT
    return None


def _first_query_value(query: dict[str, list[str]], names: tuple[str, ...]) -> str | None:
    for name in names:
        values = query.get(name)
        value = values[0].strip() if values else ""
        if value:
            return value
    return None
