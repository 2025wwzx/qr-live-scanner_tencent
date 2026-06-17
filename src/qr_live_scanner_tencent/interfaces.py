from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from qr_live_scanner_tencent.accounts.session import TencentSession


class AuthMode(StrEnum):
    ANONYMOUS = "anonymous"
    COOKIE = "cookie"
    AUTO = "auto"


class StreamFormat(StrEnum):
    FLV = "flv"
    HLS = "hls"
    UNKNOWN = "unknown"


class GameID(StrEnum):
    HONOR_OF_KINGS = "honor_of_kings"


class TencentLoginProvider(StrEnum):
    QQ = "qq"
    WECHAT = "wechat"


class StreamState(StrEnum):
    CONNECTING = "connecting"
    STREAMING = "streaming"
    RECONNECTING = "reconnecting"
    FAILED = "failed"


class QRLiveScannerError(Exception):
    """Base project error."""


class StreamResolveError(QRLiveScannerError):
    """Raised when a live stream URL cannot be resolved."""


class DecodeError(QRLiveScannerError):
    """Raised when a decoder backend fails unexpectedly."""


class AccountStoreError(QRLiveScannerError):
    """Raised when account credential storage fails."""


class AuthorizationError(QRLiveScannerError):
    """Raised when an account is not authorized for automatic confirmation."""


class ROIConfig(BaseModel):
    """Normalized ROI relative to decoded frame dimensions."""

    model_config = ConfigDict(frozen=True)

    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    width: float = Field(gt=0.0, le=1.0)
    height: float = Field(gt=0.0, le=1.0)

    @model_validator(mode="after")
    def _fits_in_frame(self) -> ROIConfig:
        if self.x + self.width > 1.0:
            msg = "ROI x + width must be <= 1.0"
            raise ValueError(msg)
        if self.y + self.height > 1.0:
            msg = "ROI y + height must be <= 1.0"
            raise ValueError(msg)
        return self

    @classmethod
    def full_frame(cls) -> ROIConfig:
        return cls(x=0.0, y=0.0, width=1.0, height=1.0)

    def to_pixels(self, frame_width: int, frame_height: int) -> tuple[int, int, int, int]:
        x = round(self.x * frame_width)
        y = round(self.y * frame_height)
        width = round(self.width * frame_width)
        height = round(self.height * frame_height)
        return x, y, width, height


DEFAULT_AGGRESSIVE_ROI = ROIConfig(x=0.375, y=0.375, width=0.25, height=0.25)


@dataclass(frozen=True, slots=True)
class StreamInfo:
    platform: str
    room_id: str
    url: str
    format: StreamFormat
    auth_mode: AuthMode
    ttl_seconds: int
    requires_cookie: bool
    headers: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class FramePacket:
    data: np.ndarray
    received_at: float
    width: int
    height: int
    sequence: int = 0


@dataclass(frozen=True, slots=True)
class QRCandidate:
    payload: str
    detected_at: float
    source_frame_received_at: float
    roi: ROIConfig
    backend: str = "unknown"


@dataclass(frozen=True, slots=True)
class AccountRef:
    uid: str
    game_id: GameID
    provider: TencentLoginProvider = TencentLoginProvider.QQ


@dataclass(frozen=True, slots=True)
class TencentAccountIndexEntry:
    """Local Tencent account index metadata without credentials."""

    uid: str
    provider: TencentLoginProvider = TencentLoginProvider.QQ
    authorized: bool = False

    def __post_init__(self) -> None:
        normalized_uid = str(self.uid).strip()
        if not normalized_uid:
            msg = "Tencent account uid is required"
            raise ValueError(msg)
        object.__setattr__(self, "uid", normalized_uid)
        object.__setattr__(self, "provider", TencentLoginProvider(str(self.provider)))


@dataclass(frozen=True, slots=True)
class TencentAccountIndexRepairResult:
    """Local Tencent account index repair summary without credentials."""

    provider: TencentLoginProvider = TencentLoginProvider.QQ
    entries: list[TencentAccountIndexEntry] = dataclass_field(default_factory=list)
    rebuilt_index: bool = False
    removed_stale_entries: int = 0

    def __post_init__(self) -> None:
        provider = TencentLoginProvider(str(self.provider))
        removed_stale_entries = int(self.removed_stale_entries)
        if removed_stale_entries < 0:
            msg = "removed stale entry count must be >= 0"
            raise ValueError(msg)
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "removed_stale_entries", removed_stale_entries)


@dataclass(frozen=True, slots=True)
class ScanResult:
    candidate: QRCandidate
    account: AccountRef
    scan_token: str


@dataclass(frozen=True, slots=True)
class ConfirmResult:
    scan: ScanResult
    success: bool
    sent_at: float
    message: str = ""


@runtime_checkable
class StreamSource(Protocol):
    async def resolve(self, room_id: str, auth_mode: AuthMode = AuthMode.AUTO) -> StreamInfo:
        """Resolve a room ID into a playable stream URL."""

    def frames(self, stream_info: StreamInfo) -> AsyncIterator[FramePacket]:
        """Yield decoded frame packets for the resolved stream."""


@runtime_checkable
class QRDecoder(Protocol):
    def decode(self, frame: FramePacket, roi: ROIConfig) -> QRCandidate | None:
        """Decode a QR candidate from one frame and ROI."""


@runtime_checkable
class QRDeduplicator(Protocol):
    def accept(self, candidate: QRCandidate) -> bool:
        """Return True only when this candidate should trigger auth."""


@runtime_checkable
class GameAuthAdapter(Protocol):
    async def scan(self, candidate: QRCandidate, account: AccountRef) -> ScanResult:
        """Notify target game service that a QR code has been scanned."""

    async def confirm(self, scan_result: ScanResult) -> ConfirmResult:
        """Confirm the scanned login request."""


@runtime_checkable
class AccountStore(Protocol):
    def get_token(self, game_id: GameID, uid: str) -> str | None:
        """Return a stored token, if present."""

    def save_token(self, game_id: GameID, uid: str, token: str, *, authorized: bool) -> None:
        """Persist a token and authorization flag."""

    def delete_token(self, game_id: GameID, uid: str) -> None:
        """Remove a stored token and its authorization flag."""

    def is_account_authorized(self, uid: str, game_id: GameID) -> bool:
        """Return whether automatic confirmation is allowed."""

    def get_tencent_session(
        self,
        uid: str,
        provider: TencentLoginProvider = TencentLoginProvider.QQ,
    ) -> TencentSession | None:
        """Return a stored Tencent session, if present."""

    def save_tencent_session(self, session: TencentSession, *, authorized: bool) -> None:
        """Persist a Tencent session and authorization flag."""

    def delete_tencent_session(
        self,
        uid: str,
        provider: TencentLoginProvider = TencentLoginProvider.QQ,
    ) -> None:
        """Remove a stored Tencent session and its authorization flag."""

    def is_tencent_authorized(
        self,
        uid: str,
        provider: TencentLoginProvider = TencentLoginProvider.QQ,
    ) -> bool:
        """Return whether a Tencent session is authorized."""

    def list_tencent_sessions(
        self,
        provider: TencentLoginProvider = TencentLoginProvider.QQ,
    ) -> list[TencentAccountIndexEntry]:
        """Return stored Tencent account metadata without credential values."""

    def repair_tencent_index(
        self,
        provider: TencentLoginProvider = TencentLoginProvider.QQ,
    ) -> TencentAccountIndexRepairResult:
        """Repair and summarize local Tencent account index metadata."""
