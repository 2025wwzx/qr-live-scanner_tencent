# Tencent QR Protocol Research

## Decision

Keep real Tencent scan/confirm disabled until the Wangzhe Rongyao QR payload,
login provider, endpoints, headers, request bodies, response schema, success
condition, and risk/device checks are validated from sanitized local captures or
compatible public documentation.

The default adapter config uses `validated_protocol=False`, so real HTTP is
rejected before any QR payload or Tencent session can be sent.

## Current V1 Scope

- Game: Wangzhe Rongyao / Honor of Kings.
- Providers: QQ by default, WeChat reserved.
- Runtime: decode-only monitoring is available; scan/confirm is mock-only.

## Required Validation

Record these fields before enabling real confirm:

- QR payload URL shape and which field is sent to scan.
- Provider routing: QQ, WeChat, or game-specific channel.
- Scan endpoint URL, HTTP method, required headers, and request body.
- Scan response schema and the local `scan_token` field.
- Confirm endpoint URL, HTTP method, required headers, and request body.
- Confirm response schema and exact success condition.
- Required credential family, refresh behavior, and credential expiry rules.
- Whether device ID, app version, risk challenge, captcha, signature, or client
  binding is required.

## Sanitized HAR Workflow

Export raw HAR only to an ignored local path such as `captures/tencent-login.har`.
Then run:

```powershell
qr-live-scanner-tencent redact-har `
  --input captures/tencent-login.har `
  --output captures/tencent-login.redacted.har
```

Inspect only the redacted file. Raw HAR, Cookie, token, QR payload, account ID,
and full query strings must stay local and must not be committed.

## Enablement Gate

Real confirm may be enabled only after all of these are true:

- A specific Wangzhe Rongyao provider config has validated endpoints, headers,
  body schema, credential family, and success condition.
- `LoginOrchestrator` checks both a stored `TencentSession` and its explicit
  local authorization flag before scan/confirm.
- GUI shows the selected account/provider authorization state before monitoring
  can attempt confirm.
- Logs and CLI output are verified to avoid leaking Cookie, token, account ID,
  QR payload, scan token, and full signed URLs.
- HTTP tests are fully mocked and do not depend on a real account.
