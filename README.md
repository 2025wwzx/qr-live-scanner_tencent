# qr-live-scanner-tencent

腾讯系游戏直播二维码监测与协议研究外壳，首版目标游戏为王者荣耀。

本项目与米哈游版 `qr-live-scanner` 独立存在：独立包名、CLI、keyring
namespace 和 GitHub 仓库。首版只提供直播流二维码识别、GUI 监测、脱敏 HAR
工具和腾讯扫码协议 mock 骨架；真实腾讯 `scan/confirm` 默认禁用。

## 当前范围

- 桌面 GUI 优先。
- v1 单游戏：王者荣耀。
- 登录渠道：默认 QQ，预留微信。
- 直播源沿用 B站与抖音。
- 自动确认链路保留 gate，但 `validated_protocol=False` 时不会发真实 HTTP。

## 安全边界

- 不实现真实 QQ/微信扫码确认。
- 不绕过验证码、风控、设备校验或签名校验。
- 不输出 Cookie、token、账号 ID、二维码 payload 或完整 HAR 正文。
- 只允许用户自有账号的本地授权研究与 mock 验证。

## 常用命令

```powershell
python -m pip install -e .[dev]
qr-live-scanner-tencent gui --dry-run
python -m pytest
python -m ruff check src tests
python -m mypy
```

## 协议研究

腾讯真实扫码协议必须先通过脱敏资料验证，验证项见
`docs/tencent-protocol-research.md`。原始 HAR、Cookie、token 和二维码 payload
不得提交到 git，也不要粘贴到聊天或 issue 中。

## Tencent Account QR Login

After `redact-har`, protocol research can extract a non-sensitive endpoint shape
summary without query values, header values, body text, Cookie, token, ticket, UID,
or QR payload:

```powershell
qr-live-scanner-tencent tencent-protocol-sample --input captures/tencent-login.redacted.har --output captures/tencent-login.sample.json --provider qq --flow account-login
qr-live-scanner-tencent tencent-protocol-note --input captures/tencent-login.sample.json --output captures/tencent-login.note.md
qr-live-scanner-tencent tencent-protocol-config-skeleton --input captures/tencent-login.sample.json --output profiles/tencent-account-login.toml
```

The generated config skeleton is local-only and keeps
`validated_protocol = false`; it does not enable real QQ/WeChat HTTP and must
not contain query strings, fragments, Cookie, token, ticket, qrsig, UID, QR
payload, or header values.

已新增独立账号登录链路：`tencent-login` 会由本项目生成 QQ/微信账号登录二维码，
确认成功后将 `TencentSession` 保存到本地 keyring 的 `qr-live-scanner-tencent`
namespace。当前真实 QQ/微信协议参数仍未验证，默认只允许 `--dry-run`、本地
`--mock-confirm` 或测试 mock；
未验证配置不会发真实 HTTP，也不会输出 Cookie、token、账号 ID、ticket 或 QR payload。

```powershell
qr-live-scanner-tencent tencent-login --provider qq --dry-run --qr-output work/tencent-login-qr.png
qr-live-scanner-tencent tencent-login --provider wechat --mock-confirm --mock-uid local-wechat-user --qr-output work/tencent-login-qr.png
qr-live-scanner-tencent tencent-status --provider qq --uid <local-account-id>
```

`--mock-confirm` 只保存本地 mock session，用于验证 keyring、GUI 账号表和
自动确认 gate；它不代表 QQ/微信真实扫码协议已验证。

### GUI 本地 mock 账号测试

用于先验证 QQ/微信账号信息保留、GUI 账号表刷新和 provider 隔离，不代表真实
QQ/微信扫码登录已接通。QQ/微信真实 HTTP 仍然禁用。

```powershell
qr-live-scanner-tencent gui --dry-run
```

打开 GUI 后：

1. 在“登录渠道”选择 QQ 或微信。
2. 从“账号管理”菜单进入“新增账号”。
3. 在 `Local mock UID` 输入本地测试账号 ID。
4. 点击 `Mock confirm`。
5. 回到主窗口后确认账号表出现该 UID，登录态显示为已保存。

后续如果已经验证出 QQ/微信账号二维码登录协议参数，可以只把非敏感元数据放在本地
TOML 中，再通过 `--protocol-config` 启用。不要把 Cookie、token、ticket、openid、
UID、二维码 payload 或任何已签名 URL 写进配置文件。

```toml
[account_qr_login.qq]
validated_protocol = true
fetch_url = "https://example.test/qq/fetch"
query_url = "https://example.test/qq/query"
app_id = "your-app-id"
```

```powershell
qr-live-scanner-tencent tencent-login --provider qq --protocol-config .\profiles\tencent-account-login.toml
```

## License

CC BY-NC 4.0。禁止任何形式的商业用途或贩卖。
