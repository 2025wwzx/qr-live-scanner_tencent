# Provide Real Tencent Values Safely

Use this note when supplying local captures for Tencent protocol research.

## Do Not Share

- Raw HAR files.
- Cookie, Authorization, token, access token, openid, qrsig, ticket, or QR URL.
- Account IDs, phone numbers, QQ numbers, WeChat identifiers, or full query strings.

## Safe Workflow

1. Export the capture to `captures/tencent-login.har`.
2. Run `qr-live-scanner-tencent redact-har --input captures/tencent-login.har --output captures/tencent-login.redacted.har`.
3. Run `qr-live-scanner-tencent tencent-protocol-sample --input captures/tencent-login.redacted.har --output captures/tencent-login.sample.json --provider qq --flow account-login`.
4. Run `qr-live-scanner-tencent tencent-protocol-note --input captures/tencent-login.sample.json --output captures/tencent-login.note.md`.
5. Run `qr-live-scanner-tencent tencent-protocol-config-skeleton --input captures/tencent-login.sample.json --output profiles/tencent-account-login.toml`.
6. Inspect only the redacted HAR, generated sample summary, generated note, and generated TOML skeleton.
7. Keep the raw file ignored by git.

The useful redacted capture should preserve request ordering, URL path shape,
method, status code, non-secret enum values, and JSON field names.
The generated TOML skeleton keeps `validated_protocol = false` and must not
contain Cookie, token, ticket, qrsig, UID, QR payload, query strings, or header
values.
