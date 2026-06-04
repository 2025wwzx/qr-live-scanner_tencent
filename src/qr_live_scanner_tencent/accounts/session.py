from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from qr_live_scanner_tencent.interfaces import TencentLoginProvider


@dataclass(frozen=True, slots=True)
class TencentSession:
    """腾讯登录态本地封装。

    该对象只用于保存用户已授权账号的本地凭证引用，字段值全部按敏感信息处理。
    `uid` 是本项目内部用于选择账号的非空字符串，`provider` 标识 QQ 或微信登录通道，
    `credentials` 保存后续真实协议验证后所需的最小凭证族。调用方不得把其中任何值写入
    GUI 状态、日志、异常消息或测试快照。
    """

    uid: str
    provider: TencentLoginProvider
    credentials: dict[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "uid", _require_text(self.uid, "Tencent uid is required"))
        object.__setattr__(self, "provider", TencentLoginProvider(str(self.provider)))
        object.__setattr__(self, "credentials", _normalize_credentials(self.credentials))

    def safe_description(self) -> str:
        """返回不包含账号、token、cookie 或二维码内容的固定描述。"""

        return f"Tencent {self.provider.value} session"


def dump_tencent_session(session: TencentSession) -> str:
    """序列化腾讯登录态；调用方负责写入系统凭证库。"""

    return json.dumps(
        {
            "uid": session.uid,
            "provider": session.provider.value,
            "credentials": session.credentials,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def load_tencent_session(raw: str) -> TencentSession:
    """从系统凭证库文本恢复腾讯登录态，解析失败时只抛固定错误。"""

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        msg = "stored Tencent session is invalid"
        raise ValueError(msg) from exc
    if not isinstance(data, dict):
        msg = "stored Tencent session is invalid"
        raise ValueError(msg)
    credentials = data.get("credentials")
    if not isinstance(credentials, dict):
        msg = "stored Tencent session is invalid"
        raise ValueError(msg)
    return TencentSession(
        uid=str(data.get("uid") or ""),
        provider=TencentLoginProvider(str(data.get("provider") or TencentLoginProvider.QQ)),
        credentials=_string_mapping(credentials),
    )


def _normalize_credentials(credentials: dict[str, str]) -> dict[str, str]:
    if not isinstance(credentials, dict):
        msg = "Tencent credentials must be a mapping"
        raise ValueError(msg)
    normalized = _string_mapping(credentials)
    if not normalized:
        msg = "Tencent credentials are required"
        raise ValueError(msg)
    return normalized


def _string_mapping(values: dict[Any, Any]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for raw_key, raw_value in values.items():
        key = _require_text(raw_key, "Tencent credential name is required")
        value = _require_text(raw_value, "Tencent credential value is required")
        normalized[key] = value
    return normalized


def _require_text(value: object, message: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(message)
    return normalized
