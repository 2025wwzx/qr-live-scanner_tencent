# Frozen V1 Interfaces

## Stream

- `StreamSource.resolve(room_id: str, auth_mode: AuthMode = AuthMode.AUTO) -> StreamInfo`
- `StreamSource.frames(stream_info: StreamInfo) -> AsyncIterator[FramePacket]`
- `StreamInfo` contains platform, room ID, URL, stream format, auth mode, TTL,
  cookie requirement, and optional request headers.

## Detection

- `ROIConfig` stores normalized `x`, `y`, `width`, `height` floats in `[0, 1]`.
- `QRDecoder.decode(frame: FramePacket, roi: ROIConfig) -> QRCandidate | None`
- `QRDeduplicator.accept(candidate: QRCandidate) -> bool`

## Auth

- `GameID.HONOR_OF_KINGS` is the only v1 game.
- `TencentLoginProvider` supports `qq` and `wechat`; GUI defaults to `qq`.
- `GameAuthAdapter.scan(candidate: QRCandidate, account: AccountRef) -> ScanResult`
- `GameAuthAdapter.confirm(scan_result: ScanResult) -> ConfirmResult`
- `AccountRef.provider` must match the adapter config provider before scan or
  confirm can run.
- `AccountStore` owns storage only:
  `get_tencent_session`, `save_tencent_session`, `delete_tencent_session`, and
  `is_tencent_authorized`.
- `LoginOrchestrator` is the only policy enforcer before scan/confirm.

## Security

- Keyring namespace is `service="qr-live-scanner-tencent"`.
- Tencent sessions use `username="tencent:{provider}:{uid}"`.
- Authorization flags use `username="authorized:tencent:{provider}:{uid}"`.
- Logs must not include Cookie, token, account ID, QR payload, scan token, or
  full signed URLs.
