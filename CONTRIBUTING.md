# Contributing

> 本项目禁止任何形式的商业用途/贩卖，违者必究。

## Clean-room rule

This project may study public projects for product behavior and high-level module
boundaries, but contributors must not copy GPL source code or port GPL
implementation details into this repository.

## Local proxy

Do not commit repository-local proxy settings. If your terminal cannot reach
GitHub directly, configure your own machine instead:

```powershell
git config --global http.proxy http://127.0.0.1:10808
git config --global https.proxy http://127.0.0.1:10808
```

If only this repository needs the proxy, use a repository-local Git config entry
and keep it out of commits:

```powershell
git config --local http.https://github.com.proxy http://127.0.0.1:10808
```

Use your actual local proxy port. CI must not depend on developer-local proxy
settings.

## Secrets and logs

- Never commit miHoYo tokens, Bilibili/Douyin cookies, passwords, QR payloads, or
  captured private traffic.
- Tests must use mocked HTTP clients and fake account stores.
- Logs must use the project redaction processor before emitting events.

## Worktrees

Parallel implementation branches should live under `.worktrees/`, which is
ignored by git.
