from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from typing import Any

import structlog

SCRUB_KEYS = frozenset(
    {
        "access_key",
        "account_id",
        "bili_jct",
        "cookie",
        "cookie_token",
        "csrf",
        "ltmid",
        "ltuid",
        "login_ticket",
        "mid",
        "payload",
        "qr_payload",
        "sessdata",
        "stmid",
        "stoken",
        "stuid",
        "token",
    }
)
SCRUB_KEY_FRAGMENTS = frozenset({"cookie", "payload", "password", "secret", "token"})


def scrub_sensitive_event(
    _logger: Any, _method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    return _scrub_mapping(event_dict)


def configure_logging() -> None:
    structlog.configure(
        processors=[
            scrub_sensitive_event,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ]
    )


def _scrub_mapping(mapping: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    for key, value in list(mapping.items()):
        lowered = str(key).lower()
        if lowered in SCRUB_KEYS or any(fragment in lowered for fragment in SCRUB_KEY_FRAGMENTS):
            mapping[key] = "[REDACTED]"
        else:
            mapping[key] = _scrub_value(value)
    return mapping


def _scrub_value(value: Any) -> Any:
    if isinstance(value, MutableMapping):
        return _scrub_mapping(value)
    if isinstance(value, Mapping):
        return _scrub_mapping(dict(value))
    if isinstance(value, list):
        return [_scrub_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_scrub_value(item) for item in value)
    return value
