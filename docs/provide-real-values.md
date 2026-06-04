# Provide Real Tencent Values Safely

Use this note when supplying local captures for Tencent protocol research.

## Do Not Share

- Raw HAR files.
- Cookie, Authorization, token, access token, openid, qrsig, ticket, or QR URL.
- Account IDs, phone numbers, QQ numbers, WeChat identifiers, or full query strings.

## Safe Workflow

1. Export the capture to `captures/tencent-login.har`.
2. Run `qr-live-scanner-tencent redact-har --input captures/tencent-login.har --output captures/tencent-login.redacted.har`.
3. Inspect only the redacted file.
4. Keep the raw file ignored by git.

The useful redacted capture should preserve request ordering, URL path shape,
method, status code, non-secret enum values, and JSON field names.
