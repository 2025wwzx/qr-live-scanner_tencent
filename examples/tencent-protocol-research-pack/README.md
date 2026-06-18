# Tencent Protocol Research Pack Example

This directory contains a fully sanitized QQ account-login research pack for local gate rehearsal.
It does not contain raw HAR data, account identifiers, credential values, signed URLs, response bodies, or QR payloads.

Run the local gates from the repository root:

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

The TOML file intentionally keeps `validated_protocol = false`. Passing these gates only proves that the local research artifacts are safe and internally complete; it does not enable real Tencent HTTP.
