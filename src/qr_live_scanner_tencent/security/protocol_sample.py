#!/usr/bin/env python3
from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

from qr_live_scanner_tencent.interfaces import TencentLoginProvider
from qr_live_scanner_tencent.security.har import (
    REDACTED_VALUE,
    SENSITIVE_KEYWORDS,
    SENSITIVE_PATH_KEYWORDS,
)

ALLOWED_TENCENT_PROTOCOL_SAMPLE_FLOWS = ("account-login", "game-scan-confirm")
PROTOCOL_SAMPLE_REDACTION_ERROR = "protocol sample input must be redacted before import"


def build_tencent_protocol_sample_from_har(
    har: dict[str, Any],
    *,
    provider: TencentLoginProvider,
    flow: str,
) -> dict[str, Any]:
    """从已脱敏 HAR 中提取腾讯协议研究所需的非敏感形状摘要。

    该函数只保留 HTTP 方法、host/path、query/header 名称、状态码和 MIME 类型。
    URL 参数值、header 值、正文文本、Cookie、token、ticket、openid、uid 等内容
    必须已经被 `redact-har` 替换为 `[REDACTED]`，否则会直接拒绝导入。

    Args:
        har: 已脱敏 HAR JSON 对象。
        provider: QQ 或微信登录渠道。
        flow: 研究流名称，目前限定为账号登录或游戏 scan/confirm。

    Returns:
        dict[str, Any]: 可提交或附到研究文档旁的非敏感协议形状摘要。
    """

    normalized_flow = _validate_flow(flow)
    entries = _har_entries(har)
    sample_entries = [
        _sample_entry(index, entry)
        for index, entry in enumerate(entries)
        if isinstance(entry, dict)
    ]
    return {
        "source": "redacted-har",
        "provider": provider.value,
        "flow": normalized_flow,
        "entries": sample_entries,
    }


def _validate_flow(flow: str) -> str:
    normalized = str(flow).strip().lower()
    if normalized not in ALLOWED_TENCENT_PROTOCOL_SAMPLE_FLOWS:
        allowed = ", ".join(ALLOWED_TENCENT_PROTOCOL_SAMPLE_FLOWS)
        msg = f"protocol sample flow must be one of: {allowed}"
        raise ValueError(msg)
    return normalized


def _har_entries(har: dict[str, Any]) -> list[Any]:
    log = har.get("log")
    if not isinstance(log, dict):
        msg = "HAR log object is required"
        raise ValueError(msg)
    entries = log.get("entries")
    if not isinstance(entries, list):
        msg = "HAR log.entries list is required"
        raise ValueError(msg)
    return entries


def _sample_entry(index: int, entry: dict[str, Any]) -> dict[str, Any]:
    request = entry.get("request")
    if not isinstance(request, dict):
        msg = "HAR entry request object is required"
        raise ValueError(msg)
    response = entry.get("response")
    if response is not None and not isinstance(response, dict):
        msg = "HAR entry response must be an object"
        raise ValueError(msg)

    _assert_redacted_payload(request)
    if isinstance(response, dict):
        _assert_redacted_payload(response)

    method = str(request.get("method") or "GET").strip().upper()
    url = _required_text(request.get("url"), "HAR request.url")
    parts = urlsplit(url)
    if parts.fragment:
        raise ValueError(PROTOCOL_SAMPLE_REDACTION_ERROR)
    _assert_redacted_url(parts.path, parts.query)

    request_headers = _named_values(request.get("headers"))
    response_headers = _named_values(response.get("headers")) if isinstance(response, dict) else []
    post_data = request.get("postData")
    content = response.get("content") if isinstance(response, dict) else None

    return {
        "index": index,
        "method": method,
        "scheme": parts.scheme,
        "host": parts.netloc,
        "path": parts.path,
        "query_keys": sorted(
            {key for key, _value in parse_qsl(parts.query, keep_blank_values=True)}
        ),
        "request_header_names": sorted(
            _normalize_name(item.get("name")) for item in request_headers
        ),
        "request_body_mime_type": _mime_type(post_data),
        "has_request_body": isinstance(post_data, dict) and bool(post_data),
        "response_status": int(response.get("status", 0)) if isinstance(response, dict) else 0,
        "response_header_names": sorted(
            _normalize_name(item.get("name")) for item in response_headers
        ),
        "response_body_mime_type": _mime_type(content),
    }


def _assert_redacted_payload(value: Any) -> None:
    if isinstance(value, dict):
        if "name" in value and "value" in value:
            name = str(value.get("name") or "")
            item_value = value.get("value")
            if _is_sensitive_key(name) and item_value != REDACTED_VALUE:
                raise ValueError(PROTOCOL_SAMPLE_REDACTION_ERROR)
        for key, child in value.items():
            key_text = str(key)
            if key_text.lower() == "text" and child not in ("", None, REDACTED_VALUE):
                raise ValueError(PROTOCOL_SAMPLE_REDACTION_ERROR)
            if _is_sensitive_key(key_text) and child != REDACTED_VALUE:
                raise ValueError(PROTOCOL_SAMPLE_REDACTION_ERROR)
            _assert_redacted_payload(child)
    elif isinstance(value, list):
        for item in value:
            _assert_redacted_payload(item)


def _assert_redacted_url(path: str, query: str) -> None:
    for segment in path.split("/"):
        decoded = unquote(segment)
        if decoded == REDACTED_VALUE:
            continue
        if _is_sensitive_path_segment(decoded):
            raise ValueError(PROTOCOL_SAMPLE_REDACTION_ERROR)

    for key, value in parse_qsl(query, keep_blank_values=True):
        if _is_sensitive_key(key) and unquote(value) != REDACTED_VALUE:
            raise ValueError(PROTOCOL_SAMPLE_REDACTION_ERROR)


def _named_values(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict) and "name" in item]


def _mime_type(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    return str(value.get("mimeType") or "").strip().lower()


def _required_text(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        msg = f"{label} is required"
        raise ValueError(msg)
    return text


def _normalize_name(value: object) -> str:
    return str(value or "").strip().lower()


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(keyword in lowered for keyword in SENSITIVE_KEYWORDS)


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
