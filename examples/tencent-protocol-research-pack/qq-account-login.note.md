# Tencent Protocol Validation Note

- Provider: `qq`
- Flow: `account-login`
- Source: `redacted-har`
- Real HTTP enabled: `false`

## Endpoint Shapes

| # | Method | Host | Path | Query Keys | Request Headers | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | `GET` | `ssl.ptlogin2.qq.com` | `/ptqrshow` | `appid` | `referer`, `user-agent` | `200` |
| 1 | `GET` | `ssl.ptlogin2.qq.com` | `/ptqrlogin` | `appid`, `ptqrtoken` | `referer`, `user-agent` | `200` |

## Validation Checklist

- [x] QR payload shape and provider routing documented
- [x] Endpoint purpose mapped to fetch, query, scan, or confirm
- [x] Required request headers documented without values
- [x] Required request body fields documented without values
- [x] Response schema and success condition documented
- [x] Credential family and expiry behavior documented
- [x] Risk, captcha, device, signature, and app-version checks documented
- [x] Real HTTP remains gated until all fields are verified

## Notes

- This pack is synthetic and sanitized; it is only for local gate rehearsal.
- Endpoint purposes are shape-only placeholders: `/ptqrshow` is the fetch shape and `/ptqrlogin` is the query shape.
- No raw HAR, credential value, QR payload, signed URL, account identifier, or response body is stored here.
- Passing readiness means the local research record is complete enough for human review; it does not enable real Tencent HTTP.
