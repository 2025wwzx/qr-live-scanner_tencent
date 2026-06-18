#!/usr/bin/env python3
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, unquote, urlparse, urlsplit, urlunsplit

from qr_live_scanner_tencent.accounts.tencent_qr_login import (
    ACCOUNT_QR_LOGIN_ALLOWED_CONFIG_FIELDS,
    ACCOUNT_QR_LOGIN_CONFIG_SECTION,
    ACCOUNT_QR_LOGIN_SENSITIVE_KEY_FRAGMENTS,
)
from qr_live_scanner_tencent.interfaces import TencentLoginProvider
from qr_live_scanner_tencent.security.har import (
    REDACTED_VALUE,
    SENSITIVE_KEYWORDS,
    SENSITIVE_PATH_KEYWORDS,
)

ALLOWED_TENCENT_PROTOCOL_SAMPLE_FLOWS = ("account-login", "game-scan-confirm")
PROTOCOL_SAMPLE_REDACTION_ERROR = "protocol sample input must be redacted before import"
PROTOCOL_SAMPLE_SCHEMA_ERROR = "protocol sample summary is invalid"
PROTOCOL_NOTE_CHECKLIST_ITEMS = (
    "QR payload shape and provider routing documented",
    "Endpoint purpose mapped to fetch, query, scan, or confirm",
    "Required request headers documented without values",
    "Required request body fields documented without values",
    "Response schema and success condition documented",
    "Credential family and expiry behavior documented",
    "Risk, captcha, device, signature, and app-version checks documented",
    "Real HTTP remains gated until all fields are verified",
)
ACCOUNT_QR_CONFIG_SKELETON_APP_ID = "TODO-verified-app-id"
ACCOUNT_QR_FETCH_ENDPOINT_KEYWORDS = (
    "fetch",
    "create",
    "show",
    "qrshow",
    "ptqrshow",
    "qrcode",
    "qrconnect",
    "authorize",
)
ACCOUNT_QR_QUERY_ENDPOINT_KEYWORDS = (
    "query",
    "poll",
    "status",
    "login",
    "ptqrlogin",
)
TENCENT_PROTOCOL_SAMPLE_ARTIFACT_FIELDS = frozenset(
    {"source", "provider", "flow", "entries"}
)
TENCENT_PROTOCOL_SAMPLE_ENTRY_ARTIFACT_FIELDS = frozenset(
    {
        "index",
        "method",
        "scheme",
        "host",
        "path",
        "query_keys",
        "request_header_names",
        "request_body_mime_type",
        "has_request_body",
        "response_status",
        "response_header_names",
        "response_body_mime_type",
    }
)
PROTOCOL_NOTE_URL_PATTERN = re.compile(r"https?://[^\s)>`\]\"']+")
PROTOCOL_NOTE_SENSITIVE_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b("
    r"access[_-]?token|authorization|cookie|login[_-]?ticket|openid|payload|"
    r"qr[_-]?payload|qrsig|scan[_-]?token|secret|ticket|token|uid"
    r")\s*[:=]"
)


@dataclass(frozen=True, slots=True)
class TencentProtocolArtifactCheckResult:
    """腾讯协议产物安全检查结果。

    该结果只包含 provider、flow 和条目数量等非敏感摘要，供 CLI 输出使用。
    Cookie、token、ticket、UID、二维码 payload 或 URL 参数值不得进入该对象。
    """

    provider: TencentLoginProvider
    flow: str
    entry_count: int


@dataclass(frozen=True, slots=True)
class TencentProtocolReadinessResult:
    """腾讯协议验证记录就绪状态。

    该对象只汇总 checklist 完成度和协议产物摘要。它不代表真实 HTTP 已启用，
    也不携带任何账号、票据、Cookie 或二维码原文。
    """

    provider: TencentLoginProvider
    flow: str
    entry_count: int
    checked_count: int
    total_count: int
    missing_items: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.missing_items


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


def render_tencent_protocol_note(sample: dict[str, Any]) -> str:
    """将非敏感协议形状摘要渲染为可填写的 Markdown 验证记录。

    输入必须是 `build_tencent_protocol_sample_from_har` 生成的 summary 结构。
    渲染结果只包含 provider、flow、endpoint 形状和固定 checklist，不包含 URL
    参数值、header 值、正文文本、Cookie、token、ticket、UID 或 `[REDACTED]`。

    Args:
        sample: `tencent-protocol-sample` 生成的 JSON 对象。

    Returns:
        str: 可保存为 `.note.md` 的研究记录模板。
    """

    provider = _sample_text(sample, "provider")
    flow = _validate_flow(_sample_text(sample, "flow"))
    source = _sample_text(sample, "source")
    if source != "redacted-har":
        raise ValueError(PROTOCOL_SAMPLE_SCHEMA_ERROR)

    entries = sample.get("entries")
    if not isinstance(entries, list):
        raise ValueError(PROTOCOL_SAMPLE_SCHEMA_ERROR)

    lines = [
        "# Tencent Protocol Validation Note",
        "",
        f"- Provider: `{provider}`",
        f"- Flow: `{flow}`",
        f"- Source: `{source}`",
        "- Real HTTP enabled: `false`",
        "",
        "## Endpoint Shapes",
        "",
        "| # | Method | Host | Path | Query Keys | Request Headers | Status |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for entry in entries:
        lines.append(_endpoint_table_row(_sample_entry_from_summary(entry)))

    lines.extend(
        [
            "",
            "## Validation Checklist",
            "",
            *[f"- [ ] {item}" for item in PROTOCOL_NOTE_CHECKLIST_ITEMS],
            "",
            "## Notes",
            "",
            "- Keep raw HAR, Cookie, token, ticket, UID, QR payload, and signed URLs out of git.",
            "- Fill endpoint purposes and success conditions only after local verification.",
            "",
        ]
    )
    return "\n".join(lines)


def check_tencent_protocol_artifacts(
    sample: dict[str, Any],
    config: dict[str, Any],
) -> TencentProtocolArtifactCheckResult:
    """检查协议 sample JSON 与账号登录 TOML 是否仍处于安全研究状态。

    该函数用于 `tencent-protocol-config-skeleton` 之后的事后护栏：它会拒绝
    手动改成 `validated_protocol = true` 的配置、带 query/fragment 的 endpoint、
    疑似凭据字段，以及不符合生成器 schema 的 sample JSON。错误消息只描述类别，
    不回显文件中的原始值。
    """

    provider, flow, entry_count = _validate_protocol_sample_artifact(sample)
    _validate_account_qr_config_artifact(config, provider=provider)
    return TencentProtocolArtifactCheckResult(
        provider=provider,
        flow=flow,
        entry_count=entry_count,
    )


def check_tencent_protocol_readiness(
    sample: dict[str, Any],
    config: dict[str, Any],
    note_text: str,
) -> TencentProtocolReadinessResult:
    """检查协议研究记录是否完成到可人工评审状态。

    函数先复用 artifact 安全检查，确保 sample/config 没有危险手改；随后只读取
    `render_tencent_protocol_note` 生成的 checklist 状态。返回 ready 只表示记录已
    填完，可进入人工复核；真实 QQ/微信 HTTP 仍保持禁用。
    """

    artifact = check_tencent_protocol_artifacts(sample, config)
    _assert_protocol_note_has_no_raw_values(note_text)
    checklist = _protocol_note_checklist_state(note_text)
    missing_items = tuple(
        item for item in PROTOCOL_NOTE_CHECKLIST_ITEMS if checklist.get(item) is not True
    )
    return TencentProtocolReadinessResult(
        provider=artifact.provider,
        flow=artifact.flow,
        entry_count=artifact.entry_count,
        checked_count=len(PROTOCOL_NOTE_CHECKLIST_ITEMS) - len(missing_items),
        total_count=len(PROTOCOL_NOTE_CHECKLIST_ITEMS),
        missing_items=missing_items,
    )


def render_tencent_account_qr_config_skeleton(sample: dict[str, Any]) -> str:
    """将账号登录协议样本渲染为安全默认的本地 TOML 配置骨架。

    输入必须来自 `tencent-protocol-sample --flow account-login`。输出只包含
    `account_qr_login.<provider>` 允许的非敏感字段，并固定
    `validated_protocol = false`，因此不会启用真实 QQ/微信 HTTP。
    """

    provider = TencentLoginProvider(_sample_text(sample, "provider"))
    flow = _validate_flow(_sample_text(sample, "flow"))
    source = _sample_text(sample, "source")
    if source != "redacted-har" or flow != "account-login":
        raise ValueError(PROTOCOL_SAMPLE_SCHEMA_ERROR)

    entries = sample.get("entries")
    if not isinstance(entries, list):
        raise ValueError(PROTOCOL_SAMPLE_SCHEMA_ERROR)

    summary_entries = [_account_config_entry_from_summary(entry) for entry in entries]
    fetch_url = _select_account_config_endpoint_url(
        summary_entries,
        provider=provider,
        keywords=ACCOUNT_QR_FETCH_ENDPOINT_KEYWORDS,
        fallback_kind="fetch",
    )
    query_url = _select_account_config_endpoint_url(
        summary_entries,
        provider=provider,
        keywords=ACCOUNT_QR_QUERY_ENDPOINT_KEYWORDS,
        fallback_kind="query",
        excluded_url=fetch_url,
    )

    lines = [
        f"[account_qr_login.{provider.value}]",
        "validated_protocol = false",
        f'fetch_url = "{_toml_string(fetch_url)}"',
        f'query_url = "{_toml_string(query_url)}"',
        f'app_id = "{ACCOUNT_QR_CONFIG_SKELETON_APP_ID}"',
        "",
    ]
    return "\n".join(lines)


def _validate_protocol_sample_artifact(
    sample: dict[str, Any],
) -> tuple[TencentLoginProvider, str, int]:
    if set(sample) != TENCENT_PROTOCOL_SAMPLE_ARTIFACT_FIELDS:
        raise ValueError("protocol sample artifact contains unsupported fields")
    if _sample_text(sample, "source") != "redacted-har":
        raise ValueError(PROTOCOL_SAMPLE_SCHEMA_ERROR)
    provider = TencentLoginProvider(_sample_text(sample, "provider"))
    flow = _validate_flow(_sample_text(sample, "flow"))
    if flow != "account-login":
        raise ValueError(PROTOCOL_SAMPLE_SCHEMA_ERROR)
    entries = sample.get("entries")
    if not isinstance(entries, list):
        raise ValueError(PROTOCOL_SAMPLE_SCHEMA_ERROR)
    for entry in entries:
        _validate_protocol_sample_artifact_entry(entry)
    return provider, flow, len(entries)


def _protocol_note_checklist_state(note_text: str) -> dict[str, bool]:
    text = _required_text(note_text, "protocol note text is required")
    checklist: dict[str, bool] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("- [") or len(line) < 7:
            continue
        marker = line[3].lower()
        if line[4:6] != "] ":
            continue
        item = line[6:].strip()
        if item in PROTOCOL_NOTE_CHECKLIST_ITEMS:
            checklist[item] = marker == "x"

    missing_template_items = [
        item for item in PROTOCOL_NOTE_CHECKLIST_ITEMS if item not in checklist
    ]
    if missing_template_items:
        raise ValueError("protocol note validation checklist is incomplete")
    return checklist


def _assert_protocol_note_has_no_raw_values(note_text: str) -> None:
    text = _required_text(note_text, "protocol note text is required")
    if PROTOCOL_NOTE_SENSITIVE_ASSIGNMENT_PATTERN.search(text):
        raise ValueError("protocol note contains unsafe raw values")
    for match in PROTOCOL_NOTE_URL_PATTERN.finditer(text):
        parsed = urlparse(match.group(0))
        if parsed.query or parsed.fragment:
            raise ValueError("protocol note contains unsafe raw values")


def _validate_protocol_sample_artifact_entry(value: Any) -> None:
    if not isinstance(value, dict):
        raise ValueError(PROTOCOL_SAMPLE_SCHEMA_ERROR)
    if set(value) != TENCENT_PROTOCOL_SAMPLE_ENTRY_ARTIFACT_FIELDS:
        raise ValueError("protocol sample artifact contains unsupported fields")

    index = value["index"]
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise ValueError(PROTOCOL_SAMPLE_SCHEMA_ERROR)
    status = value["response_status"]
    if not isinstance(status, int) or isinstance(status, bool) or status < 0:
        raise ValueError(PROTOCOL_SAMPLE_SCHEMA_ERROR)
    if not isinstance(value["has_request_body"], bool):
        raise ValueError(PROTOCOL_SAMPLE_SCHEMA_ERROR)

    scheme = _summary_text(value, "scheme").lower()
    host = _summary_text(value, "host")
    path = _summary_text(value, "path")
    _validate_artifact_endpoint_shape(scheme=scheme, host=host, path=path)

    _validate_artifact_token(_summary_text(value, "method"), "protocol sample method")
    _validate_artifact_optional_token(
        value["request_body_mime_type"],
        "protocol sample request MIME type",
    )
    _validate_artifact_optional_token(
        value["response_body_mime_type"],
        "protocol sample response MIME type",
    )
    _validate_artifact_name_list(value["query_keys"], "protocol sample query keys")
    _validate_artifact_name_list(
        value["request_header_names"],
        "protocol sample request headers",
    )
    _validate_artifact_name_list(
        value["response_header_names"],
        "protocol sample response headers",
    )


def _validate_account_qr_config_artifact(
    config: dict[str, Any],
    *,
    provider: TencentLoginProvider,
) -> None:
    _reject_sensitive_config_artifact_keys(config)
    if set(config) != {ACCOUNT_QR_LOGIN_CONFIG_SECTION}:
        raise ValueError("protocol config artifact contains unsupported fields")
    section = config.get(ACCOUNT_QR_LOGIN_CONFIG_SECTION)
    if not isinstance(section, dict):
        raise ValueError("protocol config artifact section is missing")
    allowed_providers = {item.value for item in TencentLoginProvider}
    unknown_providers = set(section) - allowed_providers
    if unknown_providers:
        raise ValueError("protocol config artifact contains unsupported provider sections")
    provider_section = section.get(provider.value)
    if not isinstance(provider_section, dict):
        raise ValueError("protocol config artifact provider section is missing")
    for raw_provider_section in section.values():
        if not isinstance(raw_provider_section, dict):
            raise ValueError("protocol config artifact provider section is invalid")
        _validate_account_qr_config_provider_artifact(raw_provider_section)


def _validate_account_qr_config_provider_artifact(provider_section: dict[str, Any]) -> None:
    unknown_fields = set(provider_section) - ACCOUNT_QR_LOGIN_ALLOWED_CONFIG_FIELDS
    if unknown_fields:
        raise ValueError("protocol config artifact contains unsupported fields")
    if provider_section.get("validated_protocol") is not False:
        raise ValueError("protocol config validated_protocol must remain false")
    _validate_artifact_endpoint_url(provider_section.get("fetch_url"))
    _validate_artifact_endpoint_url(provider_section.get("query_url"))
    _validate_artifact_token(
        _required_text(provider_section.get("app_id"), "protocol config app id is required"),
        "protocol config app id",
    )


def _validate_artifact_endpoint_url(value: object) -> None:
    endpoint_url = _required_text(value, "protocol config endpoint URL is required")
    if any(char in endpoint_url for char in "\r\n"):
        raise ValueError("protocol config endpoint URL is invalid")
    parsed = urlparse(endpoint_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("protocol config endpoint URL is invalid")
    if parsed.query or parsed.fragment:
        raise ValueError("protocol config endpoint URL must not include signed endpoint data")
    _validate_artifact_endpoint_shape(
        scheme=parsed.scheme,
        host=parsed.netloc,
        path=parsed.path,
    )


def _validate_artifact_endpoint_shape(*, scheme: str, host: str, path: str) -> None:
    if scheme not in {"http", "https"}:
        raise ValueError(PROTOCOL_SAMPLE_SCHEMA_ERROR)
    if any(char in host for char in "/?#\r\n") or any(char in path for char in "?#\r\n"):
        raise ValueError(PROTOCOL_SAMPLE_SCHEMA_ERROR)
    if not path.startswith("/"):
        raise ValueError(PROTOCOL_SAMPLE_SCHEMA_ERROR)
    _assert_redacted_url(path, "")


def _validate_artifact_name_list(value: object, label: str) -> None:
    if not isinstance(value, list):
        raise ValueError(PROTOCOL_SAMPLE_SCHEMA_ERROR)
    for item in value:
        _validate_artifact_token(_required_text(item, label), label)


def _validate_artifact_optional_token(value: object, label: str) -> None:
    if not isinstance(value, str):
        raise ValueError(PROTOCOL_SAMPLE_SCHEMA_ERROR)
    if not value:
        return
    _validate_artifact_token(value, label)


def _validate_artifact_token(value: str, label: str) -> None:
    text = _required_text(value, label)
    if any(char in text for char in "\r\n"):
        raise ValueError(PROTOCOL_SAMPLE_SCHEMA_ERROR)


def _reject_sensitive_config_artifact_keys(value: object) -> None:
    if isinstance(value, dict):
        for raw_key, raw_value in value.items():
            key = str(raw_key).strip().lower()
            if any(fragment in key for fragment in ACCOUNT_QR_LOGIN_SENSITIVE_KEY_FRAGMENTS):
                raise ValueError("protocol config artifact contains sensitive fields")
            _reject_sensitive_config_artifact_keys(raw_value)
        return
    if isinstance(value, list):
        for item in value:
            _reject_sensitive_config_artifact_keys(item)


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


def _sample_entry_from_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(PROTOCOL_SAMPLE_SCHEMA_ERROR)
    required_fields = (
        "index",
        "method",
        "host",
        "path",
        "query_keys",
        "request_header_names",
        "response_status",
    )
    if any(field not in value for field in required_fields):
        raise ValueError(PROTOCOL_SAMPLE_SCHEMA_ERROR)
    if not isinstance(value["query_keys"], list) or not isinstance(
        value["request_header_names"], list
    ):
        raise ValueError(PROTOCOL_SAMPLE_SCHEMA_ERROR)
    return value


def _account_config_entry_from_summary(value: Any) -> dict[str, Any]:
    entry = _sample_entry_from_summary(value)
    for field in ("scheme", "host", "path"):
        _summary_text(entry, field)
    return entry


def _select_account_config_endpoint_url(
    entries: list[dict[str, Any]],
    *,
    provider: TencentLoginProvider,
    keywords: tuple[str, ...],
    fallback_kind: str,
    excluded_url: str | None = None,
) -> str:
    scored_entries = [
        (
            _account_endpoint_score(entry, keywords),
            int(entry["index"]),
            _account_config_endpoint_url(entry),
        )
        for entry in entries
    ]
    scored_entries.sort(key=lambda item: (-item[0], item[1]))
    for score, _index, endpoint_url in scored_entries:
        if score <= 0:
            break
        if endpoint_url != excluded_url:
            return endpoint_url
    return _account_config_fallback_url(provider, fallback_kind)


def _account_endpoint_score(entry: dict[str, Any], keywords: tuple[str, ...]) -> int:
    path = _summary_text(entry, "path").lower()
    return sum(1 for keyword in keywords if keyword in path)


def _account_config_endpoint_url(entry: dict[str, Any]) -> str:
    scheme = _summary_text(entry, "scheme").lower()
    host = _summary_text(entry, "host")
    path = _summary_text(entry, "path")
    if scheme not in ("http", "https"):
        raise ValueError(PROTOCOL_SAMPLE_SCHEMA_ERROR)
    if any(char in host for char in "?#\r\n") or any(char in path for char in "?#\r\n"):
        raise ValueError(PROTOCOL_SAMPLE_SCHEMA_ERROR)
    if not path.startswith("/"):
        raise ValueError(PROTOCOL_SAMPLE_SCHEMA_ERROR)
    return urlunsplit((scheme, host, path, "", ""))


def _account_config_fallback_url(provider: TencentLoginProvider, kind: str) -> str:
    return f"https://example.invalid/tencent/account/{provider.value}/qr/{kind}"


def _toml_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _endpoint_table_row(entry: dict[str, Any]) -> str:
    index = int(entry["index"])
    method = _markdown_code(_summary_text(entry, "method"))
    host = _markdown_code(_summary_text(entry, "host"))
    path = _markdown_code(_summary_text(entry, "path"))
    query_keys = _code_list(entry["query_keys"])
    request_headers = _code_list(entry["request_header_names"])
    status = _markdown_code(str(int(entry["response_status"])))
    return f"| {index} | {method} | {host} | {path} | {query_keys} | {request_headers} | {status} |"


def _sample_text(sample: dict[str, Any], field: str) -> str:
    return _required_text(sample.get(field), f"protocol sample {field}")


def _summary_text(entry: dict[str, Any], field: str) -> str:
    return _required_text(entry.get(field), f"protocol sample entry {field}")


def _code_list(values: Any) -> str:
    if not isinstance(values, list):
        raise ValueError(PROTOCOL_SAMPLE_SCHEMA_ERROR)
    text_values = [str(value).strip() for value in values if str(value).strip()]
    if not text_values:
        return "-"
    return ", ".join(_markdown_code(value) for value in text_values)


def _markdown_code(value: str) -> str:
    return f"`{value.replace('`', '')}`"


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
