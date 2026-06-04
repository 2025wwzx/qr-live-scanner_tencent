from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from qr_live_scanner_tencent.interfaces import (
    AccountRef,
    AccountStore,
    AuthorizationError,
    ConfirmResult,
    GameAuthAdapter,
    QRCandidate,
    QRDeduplicator,
)


@dataclass(slots=True)
class OrchestratorMetrics:
    handled_candidates: int = 0
    duplicate_candidates: int = 0
    authorization_failures: int = 0
    scan_sent: int = 0
    confirm_sent: int = 0
    last_confirm_latency_ms: float | None = None
    confirm_latency_samples_ms: list[float] = field(default_factory=list)

    def record_confirm_latency(self, candidate: QRCandidate, confirm: ConfirmResult) -> None:
        latency_ms = max(0.0, (confirm.sent_at - candidate.source_frame_received_at) * 1000)
        self.last_confirm_latency_ms = latency_ms
        self.confirm_latency_samples_ms.append(latency_ms)

    def confirm_latency_p95_ms(self) -> float:
        if not self.confirm_latency_samples_ms:
            return 0.0
        if len(self.confirm_latency_samples_ms) == 1:
            return self.confirm_latency_samples_ms[0]
        return statistics.quantiles(self.confirm_latency_samples_ms, n=20)[18]


@dataclass(slots=True)
class LoginOrchestrator:
    account_store: AccountStore
    auth_adapter: GameAuthAdapter
    deduplicator: QRDeduplicator
    metrics: OrchestratorMetrics = field(default_factory=OrchestratorMetrics)

    async def handle_candidate(
        self, candidate: QRCandidate, account: AccountRef
    ) -> ConfirmResult | None:
        self.metrics.handled_candidates += 1
        self._ensure_account_can_confirm(account)
        if not self.deduplicator.accept(candidate):
            self.metrics.duplicate_candidates += 1
            return None

        scan = await self.auth_adapter.scan(candidate, account)
        self.metrics.scan_sent += 1
        confirm = await self.auth_adapter.confirm(scan)
        self.metrics.confirm_sent += 1
        self.metrics.record_confirm_latency(candidate, confirm)
        return confirm

    def _ensure_account_can_confirm(self, account: AccountRef) -> None:
        session = self.account_store.get_tencent_session(account.uid, account.provider)
        if session is None:
            self.metrics.authorization_failures += 1
            msg = "Tencent session is required before automatic confirmation"
            raise AuthorizationError(msg)
        if not self.account_store.is_tencent_authorized(account.uid, account.provider):
            self.metrics.authorization_failures += 1
            msg = "account is not authorized for automatic confirmation"
            raise AuthorizationError(msg)
