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
- Account login: the project can generate a local QQ/WeChat account QR login
  ticket through `TencentAccountQRLoginService`, but default configs remain
  gated with `validated_protocol=False`. Dry-run QR generation is local-only;
  confirmed sessions are saved only from mocked or explicitly validated flows.

## Account QR Login Validation

Validate these provider-account-login fields separately from game scan/confirm:

- QR creation endpoint or official OAuth/QR URL builder and required app ID.
- Poll/query endpoint, callback handling, or other confirmation mechanism.
- Confirmed status schema and account identifier field.
- Credential family saved into `TencentSession.credentials`.
- Expiry/refresh behavior and whether provider-specific risk checks are needed.

Until those fields are verified, the account QR login service must not send real
QQ/WeChat HTTP. Tests must use `httpx.MockTransport` or injected GUI services.
For local end-to-end storage and GUI gate checks, use `tencent-login
--mock-confirm --mock-uid <local-id>`; this writes a dry-run QR image and stores
a local mock `TencentSession` only.

## Local Protocol Config

After a provider account-login protocol is verified, non-sensitive metadata may
be loaded from a local TOML file:

```toml
[account_qr_login.qq]
validated_protocol = true
fetch_url = "https://example.test/qq/fetch"
query_url = "https://example.test/qq/query"
app_id = "verified-app-id"
```

The default `protocol_mode` is `json_post`, which matches the original mockable
adapter contract. QQ can opt into the first real-shape adapter after local
verification:

```toml
[account_qr_login.qq]
validated_protocol = true
protocol_mode = "qq_ptlogin"
fetch_url = "https://ssl.ptlogin2.qq.com/ptqrshow"
query_url = "https://ssl.ptlogin2.qq.com/ptqrlogin"
app_id = "verified-qq-app-id"
```

`qq_ptlogin` writes the QR image bytes returned by the fetch endpoint, carries
the runtime `qrsig` only in memory, computes `ptqrtoken`, maps `ptuiCB(...)`
poll states, and saves confirmed QQ cookies into `TencentSession.credentials`.

QQ can also opt into the official QQ Connect authorization-code QR shape after
local verification:

```toml
[account_qr_login.qq]
validated_protocol = true
protocol_mode = "qq_qrconnect"
fetch_url = "https://graph.qq.com/oauth2.0/authorize"
query_url = "https://graph.qq.com/oauth2.0/token"
redirect_uri = "http://127.0.0.1:8765/qq/callback"
app_id = "verified-qq-connect-app-id"
```

`qq_qrconnect` renders the QQ Connect authorization URL as the QR payload. If
`redirect_uri` points to `127.0.0.1`, `localhost`, or `::1`, the service starts
a temporary local callback listener, captures `code` and `state` only in memory,
then exchanges the code for a `TencentSession`. The QQ Connect app secret must
be provided through the local environment variable
`QR_LIVE_SCANNER_TENCENT_QQ_APP_SECRET`; never put it in TOML, docs, logs, or
git.

WeChat can opt into the website OAuth QR-connect shape after local verification:

```toml
[account_qr_login.wechat]
validated_protocol = true
protocol_mode = "wechat_qrconnect"
fetch_url = "https://open.weixin.qq.com/connect/qrconnect"
query_url = "https://api.weixin.qq.com/sns/oauth2/access_token"
redirect_uri = "http://127.0.0.1:8766/wechat/callback"
app_id = "verified-wechat-app-id"
```

`wechat_qrconnect` renders the OAuth authorization URL as the QR payload. If
`redirect_uri` points to `127.0.0.1`, `localhost`, or `::1`, the service starts
a temporary local callback listener, captures `code` and `state` only in memory,
then exchanges the code for a `TencentSession`. The WeChat app secret must be
provided through the local environment variable
`QR_LIVE_SCANNER_TENCENT_WECHAT_APP_SECRET`; never put it in TOML, docs, logs,
or git. If the redirect URI is a public/tunnel URL, it must forward to the same
local callback path for the CLI flow to finish.

Use it only with clean endpoint URLs:

```powershell
qr-live-scanner-tencent tencent-protocol-config-check `
  --provider qq `
  --config profiles/tencent-account-login.toml

qr-live-scanner-tencent tencent-login `
  --provider qq `
  --protocol-config profiles/tencent-account-login.toml
```

The config loader rejects sensitive field names, sensitive `app_id` values,
endpoint URLs with query strings or fragments, and sensitive endpoint path segments.
Do not place Cookie, token, ticket, openid, uid, QR payload,
credentials, session data, or signed URLs in this file.
The config check is read-only and reports `real_http=not-called`; it does not
create a QR ticket or contact Tencent.

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
For a copy-ready command list, run:

```powershell
qr-live-scanner-tencent tencent-protocol-next-steps --provider qq
```

Then run:

```powershell
qr-live-scanner-tencent redact-har `
  --input captures/tencent-login.har `
  --output captures/tencent-login.redacted.har
```

Then extract a non-sensitive shape summary for local protocol notes:

```powershell
qr-live-scanner-tencent tencent-protocol-sample `
  --input captures/tencent-login.redacted.har `
  --output captures/tencent-login.sample.json `
  --provider qq `
  --flow account-login
```

Then render a Markdown validation note:

```powershell
qr-live-scanner-tencent tencent-protocol-note `
  --input captures/tencent-login.sample.json `
  --output captures/tencent-login.note.md
```

Then render a non-sensitive local TOML skeleton for account QR login config:

```powershell
qr-live-scanner-tencent tencent-protocol-config-skeleton `
  --input captures/tencent-login.sample.json `
  --output profiles/tencent-account-login.toml
```

Then verify that the generated artifacts still contain only safe research
metadata:

```powershell
qr-live-scanner-tencent tencent-protocol-artifact-check `
  --sample captures/tencent-login.sample.json `
  --config profiles/tencent-account-login.toml
```

After every validation checklist item has been investigated and marked in the
note, run the readiness gate:

```powershell
qr-live-scanner-tencent tencent-protocol-readiness `
  --sample captures/tencent-login.sample.json `
  --config profiles/tencent-account-login.toml `
  --note captures/tencent-login.note.md
```

The sample file keeps only method, host, path, query/header names, status code,
and MIME type. It rejects unredacted Cookie, token, ticket, account ID, UID,
QR payload, request body text, and signed URL fragments before writing output.
The note file is a checklist template for recording endpoint purpose, request
body fields, response schema, success condition, credential family, and risk
checks without copying raw values.
The config skeleton keeps only provider section, `validated_protocol = false`,
clean endpoint URLs without query strings or fragments, and an app ID
placeholder. It must be manually verified and edited before it can be used for
real HTTP.
The artifact and readiness commands are local research gates only. They keep
`real_http=disabled` and do not switch `validated_protocol` to true. The
readiness gate also rejects note text that contains signed URLs, query strings,
or sensitive assignments such as `ticket=` or `Cookie:`.

Inspect only the redacted file. Raw HAR, Cookie, token, QR payload, account ID,
and full query strings must stay local and must not be committed.

## Sanitized Example Pack

Before collecting real captures, you can rehearse the local gates with the
committed sanitized pack:

```powershell
qr-live-scanner-tencent tencent-protocol-example-check

qr-live-scanner-tencent tencent-protocol-artifact-check `
  --sample examples/tencent-protocol-research-pack/qq-account-login.sample.json `
  --config examples/tencent-protocol-research-pack/qq-account-login.toml

qr-live-scanner-tencent tencent-protocol-readiness `
  --sample examples/tencent-protocol-research-pack/qq-account-login.sample.json `
  --config examples/tencent-protocol-research-pack/qq-account-login.toml `
  --note examples/tencent-protocol-research-pack/qq-account-login.note.md
```

The example pack is synthetic, keeps `validated_protocol = false`, and does not
enable real HTTP.

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
- `tencent-protocol-artifact-check` and `tencent-protocol-readiness` pass on the
  sanitized sample, TOML skeleton, and completed note before any config is
  considered for manual promotion.
