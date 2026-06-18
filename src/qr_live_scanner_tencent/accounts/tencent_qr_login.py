from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import tomllib
from dataclasses import dataclass, field, replace
from enum import StrEnum
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urlparse, urlsplit, urlunsplit

import httpx
import qrcode

from qr_live_scanner_tencent.accounts.device import LocalDeviceIdStore
from qr_live_scanner_tencent.accounts.session import TencentSession
from qr_live_scanner_tencent.interfaces import (
    AccountStore,
    AccountStoreError,
    TencentLoginProvider,
)

TENCENT_QQ_QR_FETCH_URL = "https://example.invalid/tencent/account/qq/qr/fetch"
TENCENT_QQ_QR_QUERY_URL = "https://example.invalid/tencent/account/qq/qr/query"
TENCENT_QQ_QRCONNECT_OPENID_URL = "https://graph.qq.com/oauth2.0/me"
TENCENT_WECHAT_QR_FETCH_URL = "https://example.invalid/tencent/account/wechat/qr/fetch"
TENCENT_WECHAT_QR_QUERY_URL = "https://example.invalid/tencent/account/wechat/qr/query"
TENCENT_DRY_RUN_QR_PREFIX = "qr-live-scanner-tencent://account-login/dry-run"
TENCENT_QQ_APP_SECRET_ENV = "QR_LIVE_SCANNER_TENCENT_QQ_APP_SECRET"
TENCENT_WECHAT_APP_SECRET_ENV = "QR_LIVE_SCANNER_TENCENT_WECHAT_APP_SECRET"
ACCOUNT_QR_LOGIN_CONFIG_SECTION = "account_qr_login"
ACCOUNT_QR_LOGIN_ALLOWED_CONFIG_FIELDS = frozenset(
    {"validated_protocol", "fetch_url", "query_url", "app_id", "protocol_mode", "redirect_uri"}
)
ACCOUNT_QR_LOGIN_SENSITIVE_KEY_FRAGMENTS = frozenset(
    {
        "account_id",
        "cookie",
        "credential",
        "openid",
        "password",
        "payload",
        "secret",
        "session",
        "ticket",
        "token",
        "uid",
    }
)
ACCOUNT_QR_LOGIN_SENSITIVE_VALUE_FRAGMENTS = frozenset(
    ACCOUNT_QR_LOGIN_SENSITIVE_KEY_FRAGMENTS | {"authorization", "qrsig", "scan_token"}
)
QQ_PTLOGIN_IMAGE_SIGNATURES = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")
SAFE_ENDPOINT_PATH_SEGMENTS = frozenset({"access_token", "refresh_token", "token"})
QQ_PTLOGIN_UID_COOKIE_NAMES = ("uin", "ptui_loginuin")
QQ_PTLOGIN_AUTH_COOKIE_NAMES = frozenset(
    {
        "skey",
        "p_skey",
        "pt4_token",
        "ptcz",
        "pt2gguin",
        "uin",
        "ptui_loginuin",
    }
)


class TencentAccountQRLoginState(StrEnum):
    WAITING = "waiting"
    SCANNED = "scanned"
    CONFIRMED = "confirmed"
    EXPIRED = "expired"
    FAILED = "failed"


class TencentAccountQRLoginProtocolMode(StrEnum):
    JSON_POST = "json_post"
    QQ_PTLOGIN = "qq_ptlogin"
    QQ_QRCONNECT = "qq_qrconnect"
    WECHAT_QRCONNECT = "wechat_qrconnect"


class TencentAccountQRLoginError(Exception):
    """Tencent 账号二维码登录失败；错误信息不得包含凭据、账号 ID 或二维码原文。"""


class _TencentAccountQRDryRunClient:
    async def get(self, _url: str, **_kwargs: Any) -> httpx.Response:
        msg = "Tencent account QR dry-run client cannot perform HTTP"
        raise TencentAccountQRLoginError(msg)

    async def post(self, _url: str, **_kwargs: Any) -> httpx.Response:
        msg = "Tencent account QR dry-run client cannot perform HTTP"
        raise TencentAccountQRLoginError(msg)

    async def aclose(self) -> None:
        return None


@dataclass(frozen=True, slots=True)
class TencentAccountQRLoginConfig:
    """腾讯账号二维码登录通道配置。

    首版只作为已验证协议或测试 mock 的注入点。默认配置的 `validated_protocol=False`
    会在任何真实 HTTP 之前拦截，避免在 QQ/微信参数尚未确认时误发请求。
    """

    provider: TencentLoginProvider
    name: str
    fetch_url: str
    query_url: str
    app_id: str = ""
    validated_protocol: bool = False
    protocol_mode: TencentAccountQRLoginProtocolMode = TencentAccountQRLoginProtocolMode.JSON_POST
    redirect_uri: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", TencentLoginProvider(str(self.provider)))
        object.__setattr__(
            self,
            "protocol_mode",
            TencentAccountQRLoginProtocolMode(str(self.protocol_mode)),
        )
        _ensure_protocol_mode_supported(self.provider, self.protocol_mode)
        object.__setattr__(
            self,
            "redirect_uri",
            _redirect_uri_for_mode(self.protocol_mode, self.redirect_uri),
        )

    def validated(
        self,
        *,
        fetch_url: str,
        query_url: str,
        app_id: str,
        protocol_mode: TencentAccountQRLoginProtocolMode | str | None = None,
        redirect_uri: str | None = None,
    ) -> TencentAccountQRLoginConfig:
        normalized_mode = (
            self.protocol_mode if protocol_mode is None else _protocol_mode(protocol_mode)
        )
        _ensure_protocol_mode_supported(self.provider, normalized_mode)
        normalized_redirect_uri = _redirect_uri_for_mode(
            normalized_mode,
            redirect_uri if redirect_uri is not None else self.redirect_uri,
        )
        return replace(
            self,
            fetch_url=_require_text(fetch_url, "Tencent account QR fetch URL is required"),
            query_url=_require_text(query_url, "Tencent account QR query URL is required"),
            app_id=_require_text(app_id, "Tencent account QR app id is required"),
            validated_protocol=True,
            protocol_mode=normalized_mode,
            redirect_uri=normalized_redirect_uri,
        )


def load_tencent_account_qr_login_config(
    path: str | Path,
    provider: TencentLoginProvider,
) -> TencentAccountQRLoginConfig:
    """从本地 TOML 加载已验证的腾讯账号二维码登录配置。

    配置文件只能携带非敏感协议元数据。函数会拒绝疑似凭据字段、带查询串的
    endpoint、未显式验证的配置和未知字段；错误消息不回显配置值。
    """

    config_path = Path(path)
    try:
        with config_path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        msg = "Tencent account QR config could not be loaded"
        raise TencentAccountQRLoginError(msg) from exc
    _reject_sensitive_config_keys(data)

    normalized_provider = TencentLoginProvider(str(provider))
    section = data.get(ACCOUNT_QR_LOGIN_CONFIG_SECTION)
    if not isinstance(section, dict):
        msg = (
            "Tencent account QR config section is missing "
            f"for provider={normalized_provider.value}"
        )
        raise TencentAccountQRLoginError(msg)
    provider_section = section.get(normalized_provider.value)
    if not isinstance(provider_section, dict):
        msg = (
            "Tencent account QR provider config is missing "
            f"for provider={normalized_provider.value}"
        )
        raise TencentAccountQRLoginError(msg)

    unknown_fields = set(provider_section) - ACCOUNT_QR_LOGIN_ALLOWED_CONFIG_FIELDS
    if unknown_fields:
        msg = "Tencent account QR config contains unsupported fields"
        raise TencentAccountQRLoginError(msg)
    if provider_section.get("validated_protocol") is not True:
        msg = "Tencent account QR config is not validated"
        raise TencentAccountQRLoginError(msg)

    base_config = TencentAccountQRLoginService.default_configs()[normalized_provider]
    return base_config.validated(
        fetch_url=_require_endpoint_url(provider_section.get("fetch_url")),
        query_url=_require_endpoint_url(provider_section.get("query_url")),
        app_id=_require_app_id(provider_section.get("app_id")),
        protocol_mode=_protocol_mode(
            provider_section.get(
                "protocol_mode",
                TencentAccountQRLoginProtocolMode.JSON_POST.value,
            )
        ),
        redirect_uri=(
            _require_redirect_uri(provider_section.get("redirect_uri"))
            if "redirect_uri" in provider_section
            else None
        ),
    )


@dataclass(frozen=True, slots=True)
class TencentAccountQRTicket:
    """腾讯账号二维码登录票据。

    `qr_url` 只允许用于二维码渲染，不得出现在日志、状态栏或异常消息中。`ticket`
    是轮询状态所需的敏感值，也必须只在内存和本地凭据流程内部传递。
    """

    provider: TencentLoginProvider
    app_id: str
    ticket: str
    qr_url: str
    device_id: str
    expires_in_seconds: int | None = None
    dry_run: bool = False
    qr_image_bytes: bytes | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", TencentLoginProvider(str(self.provider)))
        object.__setattr__(self, "app_id", _require_text(self.app_id, "app id is required"))
        object.__setattr__(self, "ticket", _require_text(self.ticket, "QR ticket is required"))
        object.__setattr__(self, "qr_url", _require_text(self.qr_url, "QR URL is required"))
        object.__setattr__(
            self,
            "device_id",
            _require_text(self.device_id, "device id is required"),
        )
        if self.qr_image_bytes is not None:
            object.__setattr__(
                self,
                "qr_image_bytes",
                _require_qr_image_bytes(self.qr_image_bytes),
            )

    def safe_description(self) -> str:
        mode = "dry-run " if self.dry_run else ""
        return f"Tencent {self.provider.value} {mode}account QR ticket"


@dataclass(frozen=True, slots=True)
class TencentAccountQRLoginStatus:
    """腾讯账号二维码登录状态。

    确认成功时 `session` 携带本地待保存登录态；其他状态只表示手机端扫码进度。
    所有可显示描述都必须避开 UID、Cookie、token、ticket 和 QR payload。
    """

    provider: TencentLoginProvider
    state: TencentAccountQRLoginState
    session: TencentSession | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", TencentLoginProvider(str(self.provider)))
        object.__setattr__(self, "state", TencentAccountQRLoginState(str(self.state)))

    def safe_description(self) -> str:
        return f"Tencent {self.provider.value} account QR status: {self.state.value}"


@dataclass(slots=True)
class TencentAccountQRLoginService:
    """生成并轮询腾讯账号登录二维码。

    默认配置只提供 QQ/微信通道骨架，不会真实访问 Tencent。真实接入时需要用已验证
    的 endpoint/app 参数构造 `TencentAccountQRLoginConfig.validated(...)`，测试则通过
    `httpx.MockTransport` 注入响应。
    """

    client: httpx.AsyncClient | _TencentAccountQRDryRunClient
    device_id_store: LocalDeviceIdStore
    config: TencentAccountQRLoginConfig
    oauth_callback_codes: dict[str, str] = field(default_factory=dict, repr=False)
    oauth_callback_server: asyncio.AbstractServer | None = field(
        default=None,
        init=False,
        repr=False,
    )
    oauth_callback_path: str = field(default="", init=False, repr=False)

    async def fetch_qr(self) -> TencentAccountQRTicket:
        self._ensure_protocol_validated()
        if self.config.protocol_mode is TencentAccountQRLoginProtocolMode.QQ_PTLOGIN:
            return await self._fetch_qr_qq_ptlogin()
        if self.config.protocol_mode is TencentAccountQRLoginProtocolMode.QQ_QRCONNECT:
            return await self._fetch_qr_qq_qrconnect()
        if self.config.protocol_mode is TencentAccountQRLoginProtocolMode.WECHAT_QRCONNECT:
            return await self._fetch_qr_wechat_qrconnect()
        device_id = self.device_id_store.get_or_create()
        try:
            response = await self.client.post(
                self.config.fetch_url,
                json={
                    "app_id": self.config.app_id,
                    "device": device_id,
                    "provider": self.config.provider.value,
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            msg = "Tencent account QR fetch HTTP failed"
            raise TencentAccountQRLoginError(msg) from exc

        payload = _response_payload(response, "Tencent account QR fetch response failed")
        return TencentAccountQRTicket(
            provider=self.config.provider,
            app_id=self.config.app_id,
            ticket=_parse_ticket(payload),
            qr_url=_parse_qr_url(payload),
            device_id=device_id,
            expires_in_seconds=_parse_expires_in(payload.get("expires_in")),
        )

    async def query_qr(
        self,
        ticket: str | TencentAccountQRTicket,
    ) -> TencentAccountQRLoginStatus:
        self._ensure_protocol_validated()
        if self.config.protocol_mode is TencentAccountQRLoginProtocolMode.QQ_PTLOGIN:
            return await self._query_qr_qq_ptlogin(ticket)
        if self.config.protocol_mode is TencentAccountQRLoginProtocolMode.QQ_QRCONNECT:
            return await self._query_qr_qq_qrconnect(ticket)
        if self.config.protocol_mode is TencentAccountQRLoginProtocolMode.WECHAT_QRCONNECT:
            return await self._query_qr_wechat_qrconnect(ticket)
        ticket_value = _ticket_value(ticket)
        device_id = (
            ticket.device_id
            if isinstance(ticket, TencentAccountQRTicket)
            else self.device_id_store.get_or_create()
        )
        try:
            response = await self.client.post(
                self.config.query_url,
                json={
                    "app_id": self.config.app_id,
                    "device": device_id,
                    "provider": self.config.provider.value,
                    "ticket": ticket_value,
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            msg = "Tencent account QR query HTTP failed"
            raise TencentAccountQRLoginError(msg) from exc

        payload = _response_payload(response, "Tencent account QR query response failed")
        state = _parse_state(payload.get("state", payload.get("status")))
        if state is not TencentAccountQRLoginState.CONFIRMED:
            return TencentAccountQRLoginStatus(provider=self.config.provider, state=state)
        return TencentAccountQRLoginStatus(
            provider=self.config.provider,
            state=state,
            session=_parse_confirmed_session(self.config.provider, payload),
        )

    def ticket_from_values(self, *, ticket: str, qr_url: str) -> TencentAccountQRTicket:
        return TencentAccountQRTicket(
            provider=self.config.provider,
            app_id=self.config.app_id,
            ticket=ticket,
            qr_url=qr_url,
            device_id=self.device_id_store.get_or_create(),
        )

    def dry_run_ticket(self) -> TencentAccountQRTicket:
        device_id = self.device_id_store.get_or_create()
        ticket = f"dry-run-{self.config.provider.value}-{device_id}"
        qr_url = (
            f"{TENCENT_DRY_RUN_QR_PREFIX}"
            f"?provider={self.config.provider.value}&ticket={ticket}"
        )
        return TencentAccountQRTicket(
            provider=self.config.provider,
            app_id=self.config.app_id or "dry-run",
            ticket=ticket,
            qr_url=qr_url,
            device_id=device_id,
            dry_run=True,
        )

    def write_qr_png(self, ticket: TencentAccountQRTicket, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if ticket.qr_image_bytes is not None:
            output_path.write_bytes(ticket.qr_image_bytes)
            return
        image = qrcode.make(ticket.qr_url)
        image.save(output_path)

    def accept_oauth_callback(self, *, state: str, code: str) -> None:
        state_value = _oauth_callback_value(state, "Tencent OAuth state is required")
        code_value = _oauth_callback_value(code, "Tencent OAuth code is required")
        self.oauth_callback_codes[state_value] = code_value

    def save_confirmed_session(
        self,
        status: TencentAccountQRLoginStatus,
        account_store: AccountStore,
    ) -> TencentSession:
        if status.session is None:
            msg = "Tencent account QR status does not include a confirmed session"
            raise TencentAccountQRLoginError(msg)
        try:
            account_store.save_tencent_session(status.session, authorized=True)
        except AccountStoreError as exc:
            msg = "Tencent account QR session storage failed"
            raise TencentAccountQRLoginError(msg) from exc
        return status.session

    async def _fetch_qr_qq_ptlogin(self) -> TencentAccountQRTicket:
        device_id = self.device_id_store.get_or_create()
        try:
            response = await self.client.get(
                self.config.fetch_url,
                params=_qq_ptlogin_fetch_params(self.config.app_id),
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            msg = "Tencent account QR fetch HTTP failed"
            raise TencentAccountQRLoginError(msg) from exc

        return TencentAccountQRTicket(
            provider=self.config.provider,
            app_id=self.config.app_id,
            ticket=_qq_qrsig_from_response(response),
            qr_url=self.config.fetch_url,
            device_id=device_id,
            qr_image_bytes=_require_qr_image_bytes(response.content),
        )

    async def _query_qr_qq_ptlogin(
        self,
        ticket: str | TencentAccountQRTicket,
    ) -> TencentAccountQRLoginStatus:
        qrsig = _ticket_value(ticket)
        try:
            response = await self.client.get(
                self.config.query_url,
                params=_qq_ptlogin_query_params(self.config.app_id, qrsig),
                headers={"Cookie": f"qrsig={_cookie_header_value(qrsig)}"},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            msg = "Tencent account QR query HTTP failed"
            raise TencentAccountQRLoginError(msg) from exc

        callback = _parse_qq_ptlogin_callback(response.text)
        state = _qq_ptlogin_state(callback.code)
        if state is not TencentAccountQRLoginState.CONFIRMED:
            return TencentAccountQRLoginStatus(provider=self.config.provider, state=state)
        return TencentAccountQRLoginStatus(
            provider=self.config.provider,
            state=state,
            session=_qq_session_from_response(self.config.provider, response),
        )

    async def _fetch_qr_qq_qrconnect(self) -> TencentAccountQRTicket:
        await self._start_oauth_callback_server_if_local()
        device_id = self.device_id_store.get_or_create()
        state = secrets.token_urlsafe(24)
        qr_url = _qq_qrconnect_url(
            self.config.fetch_url,
            app_id=self.config.app_id,
            redirect_uri=self.config.redirect_uri,
            state=state,
        )
        return TencentAccountQRTicket(
            provider=self.config.provider,
            app_id=self.config.app_id,
            ticket=state,
            qr_url=qr_url,
            device_id=device_id,
        )

    async def _query_qr_qq_qrconnect(
        self,
        ticket: str | TencentAccountQRTicket,
    ) -> TencentAccountQRLoginStatus:
        state = _ticket_value(ticket)
        code = self.oauth_callback_codes.get(state)
        if code is None:
            return TencentAccountQRLoginStatus(
                provider=self.config.provider,
                state=TencentAccountQRLoginState.WAITING,
            )
        try:
            response = await self.client.get(
                self.config.query_url,
                params=_qq_access_token_params(
                    self.config.app_id,
                    redirect_uri=self.config.redirect_uri,
                    code=code,
                ),
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            msg = "Tencent account QR query HTTP failed"
            raise TencentAccountQRLoginError(msg) from exc

        payload = _qq_access_token_payload(response)
        if not _optional_text(payload.get("openid")):
            payload["openid"] = await self._fetch_qq_openid(payload)
        self.oauth_callback_codes.pop(state, None)
        return TencentAccountQRLoginStatus(
            provider=self.config.provider,
            state=TencentAccountQRLoginState.CONFIRMED,
            session=_qq_oauth_session_from_payload(payload),
        )

    async def _fetch_qq_openid(self, payload: dict[str, Any]) -> str:
        access_token = _require_text(
            payload.get("access_token"),
            "Tencent QQ confirmed session failed",
        )
        try:
            response = await self.client.get(
                TENCENT_QQ_QRCONNECT_OPENID_URL,
                params={"access_token": access_token, "fmt": "json"},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            msg = "Tencent account QR query HTTP failed"
            raise TencentAccountQRLoginError(msg) from exc
        return _qq_openid_from_payload(response)

    async def _fetch_qr_wechat_qrconnect(self) -> TencentAccountQRTicket:
        await self._start_oauth_callback_server_if_local()
        device_id = self.device_id_store.get_or_create()
        state = secrets.token_urlsafe(24)
        qr_url = _wechat_qrconnect_url(
            self.config.fetch_url,
            app_id=self.config.app_id,
            redirect_uri=self.config.redirect_uri,
            state=state,
        )
        return TencentAccountQRTicket(
            provider=self.config.provider,
            app_id=self.config.app_id,
            ticket=state,
            qr_url=qr_url,
            device_id=device_id,
        )

    async def _start_oauth_callback_server_if_local(self) -> None:
        if self.oauth_callback_server is not None:
            return
        parsed = urlparse(self.config.redirect_uri)
        host = str(parsed.hostname or "")
        if host not in {"127.0.0.1", "localhost", "::1"}:
            return
        port = parsed.port
        if port is None:
            msg = "Tencent OAuth local callback port is required"
            raise TencentAccountQRLoginError(msg)
        self.oauth_callback_path = parsed.path or "/"
        try:
            self.oauth_callback_server = await asyncio.start_server(
                self._handle_oauth_callback,
                host=host,
                port=port,
            )
        except OSError as exc:
            msg = "Tencent OAuth local callback server failed"
            raise TencentAccountQRLoginError(msg) from exc

    async def _handle_oauth_callback(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        status = 400
        body = b"Tencent OAuth callback failed"
        try:
            request = await reader.readuntil(b"\r\n\r\n")
            first_line = request.splitlines()[0].decode("ascii", errors="ignore")
            method, target, _version = first_line.split(" ", 2)
            if method.upper() != "GET":
                status = 405
                body = b"Tencent OAuth callback method rejected"
            else:
                parsed = urlsplit(target)
                if parsed.path != self.oauth_callback_path:
                    status = 404
                    body = b"Tencent OAuth callback path rejected"
                else:
                    values = parse_qs(parsed.query, keep_blank_values=True)
                    code = _first_query_value(values, "code")
                    state = _first_query_value(values, "state")
                    if code and state:
                        self.accept_oauth_callback(state=state, code=code)
                        status = 200
                        body = b"Tencent OAuth callback received"
        except Exception:
            status = 400
            body = b"Tencent OAuth callback failed"
        finally:
            reason = {
                200: "OK",
                400: "Bad Request",
                404: "Not Found",
                405: "Method Not Allowed",
            }.get(status, "Bad Request")
            header = (
                f"HTTP/1.1 {status} {reason}\r\n"
                "Content-Type: text/plain; charset=utf-8\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Connection: close\r\n"
                "\r\n"
            ).encode("ascii")
            writer.write(header + body)
            await writer.drain()
            writer.close()
            await writer.wait_closed()

    async def _query_qr_wechat_qrconnect(
        self,
        ticket: str | TencentAccountQRTicket,
    ) -> TencentAccountQRLoginStatus:
        state = _ticket_value(ticket)
        code = self.oauth_callback_codes.get(state)
        if code is None:
            return TencentAccountQRLoginStatus(
                provider=self.config.provider,
                state=TencentAccountQRLoginState.WAITING,
            )
        try:
            response = await self.client.get(
                self.config.query_url,
                params=_wechat_access_token_params(self.config.app_id, code),
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            msg = "Tencent account QR query HTTP failed"
            raise TencentAccountQRLoginError(msg) from exc

        payload = _wechat_access_token_payload(response)
        self.oauth_callback_codes.pop(state, None)
        return TencentAccountQRLoginStatus(
            provider=self.config.provider,
            state=TencentAccountQRLoginState.CONFIRMED,
            session=_wechat_session_from_access_token_payload(payload),
        )

    async def aclose(self) -> None:
        if self.oauth_callback_server is not None:
            self.oauth_callback_server.close()
            await self.oauth_callback_server.wait_closed()
            self.oauth_callback_server = None
        await self.client.aclose()

    def _ensure_protocol_validated(self) -> None:
        if not self.config.validated_protocol:
            msg = (
                f"Tencent account QR login for {self.config.provider.value} is not validated; "
                "use --dry-run or inject a validated mock config"
            )
            raise TencentAccountQRLoginError(msg)

    @classmethod
    def for_provider(
        cls,
        provider: TencentLoginProvider,
        *,
        client: httpx.AsyncClient,
        device_id_store: LocalDeviceIdStore,
    ) -> TencentAccountQRLoginService:
        config = cls.default_configs()[TencentLoginProvider(str(provider))]
        return cls(client=client, device_id_store=device_id_store, config=config)

    @classmethod
    def dry_run(
        cls,
        provider: TencentLoginProvider,
        *,
        device_id_store: LocalDeviceIdStore,
    ) -> TencentAccountQRLoginService:
        config = cls.default_configs()[TencentLoginProvider(str(provider))]
        return cls(
            client=_TencentAccountQRDryRunClient(),
            device_id_store=device_id_store,
            config=config,
        )

    @staticmethod
    def default_configs() -> dict[TencentLoginProvider, TencentAccountQRLoginConfig]:
        return {
            TencentLoginProvider.QQ: TencentAccountQRLoginConfig(
                provider=TencentLoginProvider.QQ,
                name="QQ",
                fetch_url=TENCENT_QQ_QR_FETCH_URL,
                query_url=TENCENT_QQ_QR_QUERY_URL,
            ),
            TencentLoginProvider.WECHAT: TencentAccountQRLoginConfig(
                provider=TencentLoginProvider.WECHAT,
                name="WeChat",
                fetch_url=TENCENT_WECHAT_QR_FETCH_URL,
                query_url=TENCENT_WECHAT_QR_QUERY_URL,
            ),
        }


@dataclass(frozen=True, slots=True)
class _QQPtloginCallback:
    code: str
    redirect_url: str


def _protocol_mode(value: object) -> TencentAccountQRLoginProtocolMode:
    try:
        return TencentAccountQRLoginProtocolMode(str(value or "").strip())
    except ValueError as exc:
        msg = "Tencent account QR protocol mode is unsupported"
        raise TencentAccountQRLoginError(msg) from exc


def _ensure_protocol_mode_supported(
    provider: TencentLoginProvider,
    protocol_mode: TencentAccountQRLoginProtocolMode,
) -> None:
    if protocol_mode in {
        TencentAccountQRLoginProtocolMode.QQ_PTLOGIN,
        TencentAccountQRLoginProtocolMode.QQ_QRCONNECT,
    }:
        normalized_provider = TencentLoginProvider(str(provider))
        if normalized_provider is not TencentLoginProvider.QQ:
            msg = "Tencent account QR protocol mode is not supported for provider"
            raise TencentAccountQRLoginError(msg)
    if protocol_mode is TencentAccountQRLoginProtocolMode.WECHAT_QRCONNECT:
        normalized_provider = TencentLoginProvider(str(provider))
        if normalized_provider is not TencentLoginProvider.WECHAT:
            msg = "Tencent account QR protocol mode is not supported for provider"
            raise TencentAccountQRLoginError(msg)


def _redirect_uri_for_mode(
    protocol_mode: TencentAccountQRLoginProtocolMode,
    redirect_uri: str | None,
) -> str:
    if protocol_mode in {
        TencentAccountQRLoginProtocolMode.QQ_QRCONNECT,
        TencentAccountQRLoginProtocolMode.WECHAT_QRCONNECT,
    }:
        return _require_redirect_uri(redirect_uri)
    if redirect_uri is None:
        return ""
    return _require_redirect_uri(redirect_uri) if str(redirect_uri).strip() else ""


def _qq_ptlogin_fetch_params(app_id: str) -> dict[str, str]:
    return {
        "appid": _require_text(app_id, "Tencent account QR app id is required"),
        "e": "2",
        "l": "M",
        "s": "3",
        "d": "72",
        "v": "4",
        "t": "0.1",
    }


def _qq_ptlogin_query_params(app_id: str, qrsig: str) -> dict[str, str]:
    return {
        "aid": _require_text(app_id, "Tencent account QR app id is required"),
        "ptqrtoken": _qq_hash33(_require_text(qrsig, "Tencent account QR ticket is required")),
        "ptredirect": "0",
        "h": "1",
        "t": "1",
        "g": "1",
        "from_ui": "1",
        "ptlang": "2052",
        "action": "0-0-0",
        "js_type": "1",
        "pt_uistyle": "40",
    }


def _qq_hash33(value: str) -> str:
    token = 0
    for char in value:
        token += (token << 5) + ord(char)
    return str(token & 0x7FFFFFFF)


def _qq_qrsig_from_response(response: httpx.Response) -> str:
    cookies = _cookies_from_response(response)
    qrsig = cookies.get("qrsig")
    if not qrsig:
        msg = "Tencent account QR fetch response missing ticket"
        raise TencentAccountQRLoginError(msg)
    return _cookie_header_value(qrsig)


def _cookie_header_value(value: object) -> str:
    text = _require_text(value, "Tencent account QR ticket is required")
    if any(char in text for char in "\r\n;"):
        msg = "Tencent account QR ticket is invalid"
        raise TencentAccountQRLoginError(msg)
    return text


def _parse_qq_ptlogin_callback(text: str) -> _QQPtloginCallback:
    values = re.findall(r"'([^']*)'", text)
    if not values:
        msg = "Tencent account QR query response failed"
        raise TencentAccountQRLoginError(msg)
    redirect_url = values[2] if len(values) > 2 else ""
    return _QQPtloginCallback(code=values[0], redirect_url=redirect_url)


def _qq_ptlogin_state(code: str) -> TencentAccountQRLoginState:
    normalized = str(code or "").strip()
    if normalized == "0":
        return TencentAccountQRLoginState.CONFIRMED
    if normalized == "67":
        return TencentAccountQRLoginState.SCANNED
    if normalized in {"65", "68"}:
        return TencentAccountQRLoginState.EXPIRED
    if normalized == "66":
        return TencentAccountQRLoginState.WAITING
    return TencentAccountQRLoginState.FAILED


def _qq_session_from_response(
    provider: TencentLoginProvider,
    response: httpx.Response,
) -> TencentSession:
    cookies = _cookies_from_response(response)
    uid = _qq_uid_from_cookies(cookies)
    credentials = {
        f"cookie_{name}": value
        for name, value in sorted(cookies.items())
        if name in QQ_PTLOGIN_AUTH_COOKIE_NAMES
    }
    if not credentials or not any(
        name in credentials for name in ("cookie_skey", "cookie_p_skey", "cookie_pt4_token")
    ):
        msg = "Tencent account QR confirmed session failed"
        raise TencentAccountQRLoginError(msg)
    return TencentSession(uid=uid, provider=provider, credentials=credentials)


def _qq_uid_from_cookies(cookies: dict[str, str]) -> str:
    for name in QQ_PTLOGIN_UID_COOKIE_NAMES:
        value = cookies.get(name)
        if not value:
            continue
        if value.startswith("o") and value[1:].isdigit():
            return value[1:]
        return value
    msg = "Tencent account QR confirmed session failed"
    raise TencentAccountQRLoginError(msg)


def _cookies_from_response(response: httpx.Response) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for header in response.headers.get_list("set-cookie"):
        jar = SimpleCookie()
        try:
            jar.load(header)
        except Exception:
            continue
        for morsel in jar.values():
            name = str(morsel.key or "").strip().lower()
            value = str(morsel.value or "").strip()
            if name and value:
                cookies[name] = value
    return cookies


def _qq_qrconnect_url(
    endpoint_url: str,
    *,
    app_id: str,
    redirect_uri: str,
    state: str,
) -> str:
    parts = urlsplit(_require_endpoint_url(endpoint_url))
    query = urlencode(
        {
            "response_type": "code",
            "client_id": _require_app_id(app_id),
            "redirect_uri": _require_redirect_uri(redirect_uri),
            "state": _oauth_callback_value(state, "Tencent OAuth state is required"),
            "scope": "get_user_info",
        }
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def _qq_access_token_params(app_id: str, *, redirect_uri: str, code: str) -> dict[str, str]:
    return {
        "grant_type": "authorization_code",
        "client_id": _require_app_id(app_id),
        "client_secret": _qq_app_secret(),
        "code": _oauth_callback_value(code, "Tencent OAuth code is required"),
        "redirect_uri": _require_redirect_uri(redirect_uri),
        "fmt": "json",
        "need_openid": "1",
    }


def _qq_app_secret() -> str:
    return _require_text(
        os.environ.get(TENCENT_QQ_APP_SECRET_ENV),
        "Tencent QQ app secret environment is required",
    )


def _qq_access_token_payload(response: httpx.Response) -> dict[str, Any]:
    payload = _qq_oauth_response_payload(response, "Tencent QQ access token response failed")
    if _optional_text(payload.get("error")) or _optional_text(payload.get("error_description")):
        msg = "Tencent QQ access token response failed"
        raise TencentAccountQRLoginError(msg)
    if not _optional_text(payload.get("access_token")):
        msg = "Tencent QQ access token response failed"
        raise TencentAccountQRLoginError(msg)
    return payload


def _qq_openid_from_payload(response: httpx.Response) -> str:
    payload = _qq_oauth_response_payload(response, "Tencent QQ openid response failed")
    if _optional_text(payload.get("error")) or _optional_text(payload.get("error_description")):
        msg = "Tencent QQ openid response failed"
        raise TencentAccountQRLoginError(msg)
    return _require_text(payload.get("openid"), "Tencent QQ openid response failed")


def _qq_oauth_response_payload(response: httpx.Response, message: str) -> dict[str, Any]:
    text = response.text.strip()
    if not text:
        raise TencentAccountQRLoginError(message)
    try:
        data = response.json()
    except ValueError:
        data = _parse_qq_oauth_text_response(text, message)
    if not isinstance(data, dict):
        raise TencentAccountQRLoginError(message)
    return data


def _parse_qq_oauth_text_response(text: str, message: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("callback(") and stripped.endswith(");"):
        stripped = stripped[len("callback(") : -2].strip()
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise TencentAccountQRLoginError(message) from exc
        if not isinstance(data, dict):
            raise TencentAccountQRLoginError(message)
        return data
    parsed = parse_qs(stripped, keep_blank_values=True)
    if not parsed:
        raise TencentAccountQRLoginError(message)
    return {key: values[0] for key, values in parsed.items() if values}


def _qq_oauth_session_from_payload(payload: dict[str, Any]) -> TencentSession:
    openid = _require_text(payload.get("openid"), "Tencent QQ confirmed session failed")
    credentials: dict[str, str] = {
        "access_token": _require_text(
            payload.get("access_token"),
            "Tencent QQ confirmed session failed",
        ),
        "openid": openid,
    }
    optional_fields = ("refresh_token", "scope", "expires_in", "client_id")
    for field_name in optional_fields:
        value = _optional_text(payload.get(field_name))
        if value:
            credentials[field_name] = value
    return TencentSession(
        uid=openid,
        provider=TencentLoginProvider.QQ,
        credentials=credentials,
    )


def _wechat_qrconnect_url(
    endpoint_url: str,
    *,
    app_id: str,
    redirect_uri: str,
    state: str,
) -> str:
    parts = urlsplit(_require_endpoint_url(endpoint_url))
    query = urlencode(
        {
            "appid": _require_app_id(app_id),
            "redirect_uri": _require_redirect_uri(redirect_uri),
            "response_type": "code",
            "scope": "snsapi_login",
            "state": _oauth_callback_value(state, "Tencent OAuth state is required"),
        }
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, "wechat_redirect"))


def _wechat_access_token_params(app_id: str, code: str) -> dict[str, str]:
    return {
        "appid": _require_app_id(app_id),
        "secret": _wechat_app_secret(),
        "code": _oauth_callback_value(code, "Tencent OAuth code is required"),
        "grant_type": "authorization_code",
    }


def _wechat_app_secret() -> str:
    return _require_text(
        os.environ.get(TENCENT_WECHAT_APP_SECRET_ENV),
        "Tencent WeChat app secret environment is required",
    )


def _wechat_access_token_payload(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        msg = "Tencent WeChat access token response failed"
        raise TencentAccountQRLoginError(msg) from exc
    if not isinstance(data, dict):
        msg = "Tencent WeChat access token response failed"
        raise TencentAccountQRLoginError(msg)
    errcode = data.get("errcode")
    if errcode not in (None, 0, "0"):
        msg = "Tencent WeChat access token response failed"
        raise TencentAccountQRLoginError(msg)
    return data


def _wechat_session_from_access_token_payload(payload: dict[str, Any]) -> TencentSession:
    openid = _require_text(payload.get("openid"), "Tencent WeChat confirmed session failed")
    unionid = str(payload.get("unionid") or "").strip()
    uid = unionid or openid
    credentials: dict[str, str] = {
        "access_token": _require_text(
            payload.get("access_token"),
            "Tencent WeChat confirmed session failed",
        ),
        "openid": openid,
    }
    optional_fields = ("refresh_token", "scope", "unionid", "expires_in")
    for field_name in optional_fields:
        value = payload.get(field_name)
        if value is not None and str(value).strip():
            credentials[field_name] = str(value).strip()
    return TencentSession(
        uid=uid,
        provider=TencentLoginProvider.WECHAT,
        credentials=credentials,
    )


def _oauth_callback_value(value: object, message: str) -> str:
    text = _require_text(value, message)
    if any(char in text for char in "\r\n"):
        raise TencentAccountQRLoginError(message)
    return text


def _first_query_value(values: dict[str, list[str]], name: str) -> str:
    candidates = values.get(name, [])
    if not candidates:
        return ""
    return str(candidates[0] or "").strip()


def _response_payload(response: httpx.Response, message: str) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise TencentAccountQRLoginError(message) from exc
    if not isinstance(data, dict):
        raise TencentAccountQRLoginError(message)
    retcode = data.get("retcode") or 0
    if not isinstance(retcode, int | str):
        raise TencentAccountQRLoginError(message)
    try:
        parsed_retcode = int(retcode)
    except ValueError as exc:
        raise TencentAccountQRLoginError(message) from exc
    if parsed_retcode != 0:
        raise TencentAccountQRLoginError(message)
    payload = data.get("data")
    if not isinstance(payload, dict):
        raise TencentAccountQRLoginError(message)
    return payload


def _parse_ticket(payload: dict[str, Any]) -> str:
    ticket = _require_text(
        payload.get("ticket"),
        "Tencent account QR fetch response missing ticket",
    )
    return ticket


def _parse_qr_url(payload: dict[str, Any]) -> str:
    raw = payload.get("qr_url", payload.get("url"))
    return _require_text(raw, "Tencent account QR fetch response missing URL")


def _parse_expires_in(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int | str):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _require_qr_image_bytes(value: object) -> bytes:
    if not isinstance(value, bytes) or not value:
        msg = "Tencent account QR fetch response missing image"
        raise TencentAccountQRLoginError(msg)
    if not any(value.startswith(signature) for signature in QQ_PTLOGIN_IMAGE_SIGNATURES):
        msg = "Tencent account QR fetch response missing image"
        raise TencentAccountQRLoginError(msg)
    return value


def _parse_state(value: object) -> TencentAccountQRLoginState:
    raw = str(value or "").strip().lower()
    if raw in {"created", "init", "waiting", "pending"}:
        return TencentAccountQRLoginState.WAITING
    if raw in {"scanned", "scan"}:
        return TencentAccountQRLoginState.SCANNED
    if raw in {"confirmed", "confirm", "success", "authorized"}:
        return TencentAccountQRLoginState.CONFIRMED
    if raw in {"expired", "timeout"}:
        return TencentAccountQRLoginState.EXPIRED
    return TencentAccountQRLoginState.FAILED


def _parse_confirmed_session(
    provider: TencentLoginProvider,
    payload: dict[str, Any],
) -> TencentSession:
    raw_session = payload.get("session")
    if isinstance(raw_session, dict):
        uid = _require_text(raw_session.get("uid", payload.get("uid")), "confirmed session failed")
        credentials = _credentials_from_mapping(raw_session.get("credentials"))
        return TencentSession(uid=uid, provider=provider, credentials=credentials)

    raw_credentials = payload.get("credentials")
    if isinstance(raw_credentials, str):
        try:
            raw_credentials = json.loads(raw_credentials)
        except json.JSONDecodeError as exc:
            msg = "Tencent account QR confirmed session failed"
            raise TencentAccountQRLoginError(msg) from exc
    uid = _require_text(payload.get("uid"), "Tencent account QR confirmed session failed")
    credentials = _credentials_from_mapping(raw_credentials)
    return TencentSession(uid=uid, provider=provider, credentials=credentials)


def _credentials_from_mapping(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        msg = "Tencent account QR confirmed session failed"
        raise TencentAccountQRLoginError(msg)
    credentials: dict[str, str] = {}
    for raw_key, raw_value in raw.items():
        key = _require_text(raw_key, "Tencent account credential name is required")
        value = _require_text(raw_value, "Tencent account credential value is required")
        credentials[key] = value
    if "openid" not in credentials:
        msg = "Tencent account QR confirmed session failed"
        raise TencentAccountQRLoginError(msg)
    return credentials


def _ticket_value(ticket: str | TencentAccountQRTicket) -> str:
    if isinstance(ticket, TencentAccountQRTicket):
        return _require_text(ticket.ticket, "Tencent account QR ticket is required")
    return _require_text(ticket, "Tencent account QR ticket is required")


def _reject_sensitive_config_keys(value: object) -> None:
    if isinstance(value, dict):
        for raw_key, raw_value in value.items():
            key = str(raw_key).strip().lower()
            if any(fragment in key for fragment in ACCOUNT_QR_LOGIN_SENSITIVE_KEY_FRAGMENTS):
                msg = "Tencent account QR config contains sensitive fields"
                raise TencentAccountQRLoginError(msg)
            _reject_sensitive_config_keys(raw_value)
        return
    if isinstance(value, list):
        for item in value:
            _reject_sensitive_config_keys(item)


def _require_endpoint_url(value: object) -> str:
    endpoint_url = _require_text(value, "Tencent account QR endpoint URL is required")
    if "\r" in endpoint_url or "\n" in endpoint_url:
        msg = "Tencent account QR endpoint URL is invalid"
        raise TencentAccountQRLoginError(msg)
    parsed = urlparse(endpoint_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        msg = "Tencent account QR endpoint URL is invalid"
        raise TencentAccountQRLoginError(msg)
    if parsed.query or parsed.fragment:
        msg = "Tencent account QR endpoint URL must not include signed endpoint data"
        raise TencentAccountQRLoginError(msg)
    if _endpoint_path_contains_sensitive_data(parsed.path):
        msg = "Tencent account QR endpoint URL must not include signed endpoint data"
        raise TencentAccountQRLoginError(msg)
    return endpoint_url


def _require_app_id(value: object) -> str:
    app_id = _require_text(value, "Tencent account QR app id is required")
    if "\r" in app_id or "\n" in app_id:
        msg = "Tencent account QR app id is invalid"
        raise TencentAccountQRLoginError(msg)
    lowered = app_id.lower()
    if "://" in lowered or "?" in app_id or "#" in app_id:
        msg = "Tencent account QR app id must not include signed endpoint data"
        raise TencentAccountQRLoginError(msg)
    if any(fragment in lowered for fragment in ACCOUNT_QR_LOGIN_SENSITIVE_VALUE_FRAGMENTS):
        msg = "Tencent account QR app id must not include credential data"
        raise TencentAccountQRLoginError(msg)
    return app_id


def _require_redirect_uri(value: object) -> str:
    redirect_uri = _require_text(value, "Tencent account QR redirect URI is required")
    if "\r" in redirect_uri or "\n" in redirect_uri:
        msg = "Tencent account QR redirect URI is invalid"
        raise TencentAccountQRLoginError(msg)
    parsed = urlparse(redirect_uri)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        msg = "Tencent account QR redirect URI is invalid"
        raise TencentAccountQRLoginError(msg)
    if parsed.query or parsed.fragment:
        msg = "Tencent account QR redirect URI must not include signed endpoint data"
        raise TencentAccountQRLoginError(msg)
    if _endpoint_path_contains_sensitive_data(parsed.path):
        msg = "Tencent account QR redirect URI must not include signed endpoint data"
        raise TencentAccountQRLoginError(msg)
    return redirect_uri


def _endpoint_path_contains_sensitive_data(path: str) -> bool:
    for segment in path.split("/"):
        decoded = unquote(segment).strip().lower()
        if not decoded:
            continue
        if decoded in SAFE_ENDPOINT_PATH_SEGMENTS:
            continue
        if any(fragment in decoded for fragment in ACCOUNT_QR_LOGIN_SENSITIVE_KEY_FRAGMENTS):
            return True
        if decoded.isdigit() and len(decoded) >= 5:
            return True
        compact = decoded.translate(str.maketrans("", "", "-_.~"))
        if (
            len(compact) >= 16
            and any(char.isalpha() for char in compact)
            and any(char.isdigit() for char in compact)
        ):
            return True
    return False


def _require_text(value: object, message: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise TencentAccountQRLoginError(message)
    return text


def _optional_text(value: object) -> str:
    return str(value or "").strip()
