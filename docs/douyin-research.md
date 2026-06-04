# Douyin Stream Research Spike

## Decision

Use browser interception as the v1 real-source route, with a mockable web-room
resolver boundary kept for tests and optional future signed URL factories. The
implementation accepts JSON shaped like Douyin's web room response and extracts
FLV/HLS URLs, but does not copy or implement third-party `a_bogus` or `X-Bogus`
signing algorithms.

## Open-Source Leads

- [`ihmily/DouyinLiveRecorder`](https://github.com/ihmily/DouyinLiveRecorder)
  documents and implements a Douyin live recording route with an `ab_sign.py`
  style signing helper. Its repository license is MIT, but this project should
  still treat it as reference behavior and reimplement only the narrow factory
  interface required here.
- [`Evil0ctal/Douyin_TikTok_Download_API`](https://github.com/Evil0ctal/Douyin_TikTok_Download_API)
  contains Douyin web anti-bot signing research such as `a_bogus` helpers. Its
  repository license is Apache-2.0. If used, vendor attribution and license
  obligations must be reviewed first; prefer a small clean adapter over copying
  broad crawler code.
- Public issue threads around `a_bogus` repeatedly note that signatures may be
  per-request and may depend on user-agent, params, cookies, or browser-derived
  state. A static URL template is not a reliable production strategy.

## Findings

- The maintained recorder reference resolves Douyin web rooms through
  `https://live.douyin.com/webcast/room/web/enter/`.
- Requests include a `web_rid` and a generated anti-bot signature such as
  `a_bogus`; some fallback flows require `X-Bogus`, cookies, or redirect-based
  room ID discovery.
- The actual stream payload contains `stream_url.flv_pull_url` and
  `stream_url.hls_pull_url_map` maps when the room is live.

## Implementation Boundary

- `DouyinStreamSource` supports injected `signed_enter_url_factory` for mocks
  and optional clean-room factory experiments.
- `DouyinBrowserStreamSource` supports a Playwright-backed browser interception
  path. It opens a real Douyin live page and captures the browser-generated
  `/webcast/room/web/enter/` response instead of implementing `a_bogus`.
- CI tests mock the full response body and do not require real cookies.
- If no signed URL factory is supplied to the factory-based source, it raises a
  clear `StreamResolveError` instead of guessing at anti-bot signatures.
- The public CLI currently rejects `smoke-run --mode real --platform douyin`
  unless `--browser-resolver` is explicitly set. This prevents users from
  mistaking the mockable resolver boundary for a validated real Douyin smoke
  path.

## Next Implementation Route

1. Prefer the browser interception route for the first real Douyin smoke.
2. Keep CI fully mocked: response matching, JSON parsing, and browser adapter
   tests must not launch Chrome.
3. Run one manual smoke with a user-provided Douyin live room URL and an
   isolated browser profile.
4. Treat pure Python `a_bogus` generation as optional follow-up work after the
   browser path is validated.
5. Never place Douyin cookies, generated signed URLs, or full query strings in
   logs or fixtures.
