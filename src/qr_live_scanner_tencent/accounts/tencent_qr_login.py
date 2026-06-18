from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

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
TENCENT_WECHAT_QR_FETCH_URL = "https://example.invalid/tencent/account/wechat/qr/fetch"
TENCENT_WECHAT_QR_QUERY_URL = "https://example.invalid/tencent/account/wechat/qr/query"
TENCENT_DRY_RUN_QR_PREFIX = "qr-live-scanner-tencent://account-login/dry-run"
ACCOUNT_QR_LOGIN_CONFIG_SECTION = "account_qr_login"
ACCOUNT_QR_LOGIN_ALLOWED_CONFIG_FIELDS = frozenset(
    {"validated_protocol", "fetch_url", "query_url", "app_id"}
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


class TencentAccountQRLoginState(StrEnum):
    WAITING = "waiting"
    SCANNED = "scanned"
    CONFIRMED = "confirmed"
    EXPIRED = "expired"
    FAILED = "failed"


class TencentAccountQRLoginError(Exception):
    """Tencent 账号二维码登录失败；错误信息不得包含凭据、账号 ID 或二维码原文。"""


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

    def validated(
        self,
        *,
        fetch_url: str,
        query_url: str,
        app_id: str,
    ) -> TencentAccountQRLoginConfig:
        return replace(
            self,
            fetch_url=_require_text(fetch_url, "Tencent account QR fetch URL is required"),
            query_url=_require_text(query_url, "Tencent account QR query URL is required"),
            app_id=_require_text(app_id, "Tencent account QR app id is required"),
            validated_protocol=True,
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
        msg = "Tencent account QR config section is missing"
        raise TencentAccountQRLoginError(msg)
    provider_section = section.get(normalized_provider.value)
    if not isinstance(provider_section, dict):
        msg = "Tencent account QR provider config is missing"
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
        app_id=_require_text(
            provider_section.get("app_id"),
            "Tencent account QR app id is required",
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

    client: httpx.AsyncClient
    device_id_store: LocalDeviceIdStore
    config: TencentAccountQRLoginConfig

    async def fetch_qr(self) -> TencentAccountQRTicket:
        self._ensure_protocol_validated()
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
        image = qrcode.make(ticket.qr_url)
        image.save(output_path)

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

    async def aclose(self) -> None:
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
        return cls(client=httpx.AsyncClient(), device_id_store=device_id_store, config=config)

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


def _endpoint_path_contains_sensitive_data(path: str) -> bool:
    for segment in path.split("/"):
        decoded = unquote(segment).strip().lower()
        if not decoded:
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
