#!/usr/bin/env python3
from __future__ import annotations

from copy import deepcopy
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

SENSITIVE_KEYWORDS = (
    "access_key",
    "account_id",
    "authorization",
    "bili_jct",
    "cookie",
    "cookie_token",
    "csrf",
    "login_ticket",
    "mid",
    "payload",
    "qr",
    "scan_token",
    "secret",
    "sessdata",
    "stoken",
    "token",
    "uid",
)
SENSITIVE_PATH_KEYWORDS = (
    "access_key",
    "account_id",
    "authorization",
    "bili_jct",
    "cookie",
    "cookie_token",
    "csrf",
    "login_ticket",
    "mid",
    "payload",
    "scan_token",
    "secret",
    "sessdata",
    "stoken",
    "ticket",
    "token",
    "uid",
)
REDACTED_VALUE = "[REDACTED]"


def redact_har(har: dict[str, Any]) -> dict[str, Any]:
    """返回脱敏后的 HAR 副本。

    函数只处理已加载到内存的 HAR 字典，不访问网络、不修改输入对象。它会递归清洗
    常见敏感字段、HTTP header、URL 查询参数、HAR queryString 条目以及请求/响应
    正文文本，避免 Token、Cookie、二维码 payload 和 UID 进入后续协议研究资料。

    Args:
        har: 浏览器导出的 HAR JSON 对象。

    Returns:
        dict[str, Any]: 可安全保存到本地研究目录的脱敏副本。
    """

    redacted = _redact_value(deepcopy(har), parent_key="")
    if not isinstance(redacted, dict):
        msg = "HAR root must be a JSON object"
        raise ValueError(msg)
    return redacted


def _redact_value(value: Any, *, parent_key: str) -> Any:
    if isinstance(value, dict):
        return _redact_mapping(value)
    if isinstance(value, list):
        return [_redact_value(item, parent_key=parent_key) for item in value]
    if isinstance(value, str):
        if parent_key.lower() == "url":
            return _redact_url(value)
        if parent_key.lower() == "text":
            return REDACTED_VALUE
        if _is_sensitive_key(parent_key):
            return REDACTED_VALUE
    return value


def _redact_mapping(mapping: dict[str, Any]) -> dict[str, Any]:
    if _looks_like_named_value(mapping):
        name = str(mapping.get("name") or "")
        if _is_sensitive_key(name):
            mapping["value"] = REDACTED_VALUE
        return {key: _redact_value(value, parent_key=key) for key, value in mapping.items()}

    redacted: dict[str, Any] = {}
    for key, value in mapping.items():
        key_text = str(key)
        if _is_sensitive_key(key_text):
            redacted[key] = REDACTED_VALUE
        else:
            redacted[key] = _redact_value(value, parent_key=key_text)
    return redacted


def _looks_like_named_value(mapping: dict[str, Any]) -> bool:
    return "name" in mapping and "value" in mapping


def _redact_url(url: str) -> str:
    parts = urlsplit(url)
    path = _redact_path(parts.path)
    if not parts.query:
        return urlunsplit((parts.scheme, parts.netloc, path, "", parts.fragment))
    query = [
        (key, REDACTED_VALUE if _is_sensitive_key(key) else value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urlunsplit((parts.scheme, parts.netloc, path, urlencode(query), parts.fragment))


def _redact_path(path: str) -> str:
    segments = [
        REDACTED_VALUE if _is_sensitive_path_segment(unquote(segment)) else segment
        for segment in path.split("/")
    ]
    return "/".join(segments)


def _is_sensitive_path_segment(segment: str) -> bool:
    lowered = segment.lower()
    if any(keyword in lowered for keyword in SENSITIVE_PATH_KEYWORDS):
        return True
    if lowered.isdigit() and len(lowered) >= 5:
        return True

    compact = lowered.translate(str.maketrans("", "", "-_.~"))
    return (
        len(compact) >= 16
        and any(char.isalpha() for char in compact)
        and any(char.isdigit() for char in compact)
    )


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(keyword in lowered for keyword in SENSITIVE_KEYWORDS)
