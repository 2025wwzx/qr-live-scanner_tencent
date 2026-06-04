from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DEVICE_ID_PATH = Path("profiles/tencent/device_id")
DEVICE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


@dataclass(frozen=True, slots=True)
class LocalDeviceIdStore:
    """管理本机专用腾讯协议研究 device id。

    首次使用时生成项目本地 32 位小写 hex 标识，之后从同一路径复用。
    该值不是账号凭证，只用于保持本机登录请求的设备参数稳定。
    """

    path: Path
    fixed_value: str | None = None

    @classmethod
    def default(cls) -> LocalDeviceIdStore:
        return cls(path=DEFAULT_DEVICE_ID_PATH)

    @classmethod
    def fixed(cls, value: str) -> LocalDeviceIdStore:
        return cls(path=Path("__fixed_device_id__"), fixed_value=_require_device_id(value))

    def get_or_create(self) -> str:
        if self.fixed_value is not None:
            return self.fixed_value
        try:
            existing = self.path.read_text(encoding="utf-8").strip()
        except OSError:
            existing = ""
        if _is_valid_device_id(existing):
            return existing

        device_id = secrets.token_hex(16)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(device_id, encoding="utf-8")
        return device_id


def _require_device_id(value: str) -> str:
    normalized = str(value).strip()
    if not _is_valid_device_id(normalized):
        msg = "device id must be 32 lowercase hex characters"
        raise ValueError(msg)
    return normalized


def _is_valid_device_id(value: str) -> bool:
    return bool(DEVICE_ID_PATTERN.fullmatch(value))
