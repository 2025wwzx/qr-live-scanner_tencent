# Provide Real Tencent Values Safely

Use this note when supplying local captures for Tencent protocol research.

## Do Not Share

- Raw HAR files.
- Cookie, Authorization, token, access token, openid, qrsig, ticket, or QR URL.
- Account IDs, phone numbers, QQ numbers, WeChat identifiers, or full query strings.

## Safe Workflow

1. Run `qr-live-scanner-tencent tencent-protocol-preflight`.
2. Run `qr-live-scanner-tencent tencent-protocol-guide --provider qq` or `--provider wechat`.
3. Export the capture to `captures/tencent-login.har`.
4. Run `qr-live-scanner-tencent redact-har --input captures/tencent-login.har --output captures/tencent-login.redacted.har`.
5. Run `qr-live-scanner-tencent tencent-protocol-sample --input captures/tencent-login.redacted.har --output captures/tencent-login.sample.json --provider qq --flow account-login`.
6. Run `qr-live-scanner-tencent tencent-protocol-note --input captures/tencent-login.sample.json --output captures/tencent-login.note.md`.
7. Run `qr-live-scanner-tencent tencent-protocol-config-skeleton --input captures/tencent-login.sample.json --output profiles/tencent-account-login.toml`.
8. Run `qr-live-scanner-tencent tencent-protocol-artifact-check --sample captures/tencent-login.sample.json --config profiles/tencent-account-login.toml`.
9. Fill only the checklist in `captures/tencent-login.note.md`; do not paste raw values.
10. Run `qr-live-scanner-tencent tencent-protocol-readiness --sample captures/tencent-login.sample.json --config profiles/tencent-account-login.toml --note captures/tencent-login.note.md`.
11. Inspect only the redacted HAR, generated sample summary, generated note, and generated TOML skeleton.
12. Keep the raw file ignored by git.

The useful redacted capture should preserve request ordering, URL path shape,
method, status code, non-secret enum values, and JSON field names.
The preflight command checks both the required `.gitignore` rules and Git's
effective ignore result for sensitive capture/config paths. It also fails if
those sensitive paths are already tracked by Git.
The generated TOML skeleton keeps `validated_protocol = false` and must not
contain Cookie, token, ticket, qrsig, UID, QR payload, query strings, or header
values.
The artifact check is a post-generation guard: it fails if the generated sample
or TOML skeleton was hand-edited to include signed URLs, sensitive fields,
raw values, or `validated_protocol = true`.
The readiness command checks that all generated checklist items are marked
complete and rejects signed URLs or sensitive assignments in the note while
still reporting `real_http=disabled`; it does not enable a real Tencent login
flow.
