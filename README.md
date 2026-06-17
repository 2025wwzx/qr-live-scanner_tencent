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
qr-live-scanner-tencent tencent-delete --provider qq --uid <local-account-id>
qr-live-scanner-tencent tencent-account-smoke --provider wechat --uid local-wechat-user --cleanup
```

`--mock-confirm` 只保存本地 mock session，用于验证 keyring、GUI 账号表和
自动确认 gate；它不代表 QQ/微信真实扫码协议已验证。`--mock-confirm` 默认不会覆盖已有同
provider/UID 的 TencentSession；如果要复用测试 UID，请先运行 `tencent-delete` 清理。
`tencent-delete` 只清除本地保存的 TencentSession 和授权标记，不连接腾讯服务，
也不会输出账号 ID、Cookie、token、ticket 或二维码 payload。
`tencent-account-smoke` 只做本地保存、查询和可选清理，不创建真实 QR 登录服务，
适合快速确认 QQ/微信账号信息保留链路。它默认不会覆盖已有同 provider/UID 的
TencentSession；如果要复用测试 UID，请先运行 `tencent-delete` 清理。

### 完整本地账号保留测试

下面这条链路只写入本机 keyring 和 GUI 状态，不连接腾讯服务，也不会生成真实
QQ/微信登录二维码。为了让 GUI 能导入已保存账号，保存步骤不要加 `--cleanup`：

```powershell
qr-live-scanner-tencent tencent-login --provider wechat --mock-confirm --mock-uid local-wechat-user --qr-output work/tencent-login-qr.png
qr-live-scanner-tencent tencent-status --provider wechat --uid local-wechat-user
qr-live-scanner-tencent gui --dry-run
qr-live-scanner-tencent tencent-delete --provider wechat --uid local-wechat-user
```

手动打开 GUI 后，在同一个登录渠道选择“账号管理” -> “导入已保存账号”，输入
`local-wechat-user`，确认账号表出现该 UID 且登录态为“已保存”。测试结束后用
`tencent-delete` 清理同 provider/UID 的本地 session；命令输出不会显示 UID、
Cookie、token、ticket 或二维码 payload。

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

GUI 的 `Mock confirm` 也不会覆盖已有同 provider/UID 的 TencentSession；如需复用测试
UID，请先删除该 provider/UID 的本地 session。

如果已经先用 `tencent-login --mock-confirm` 在 CLI 保存过账号，可以在同一个
登录渠道下从“账号管理”菜单进入“导入已保存账号”，输入本地测试账号 ID，将
该账号加入 GUI 账号列表。导入过程只检查本机 keyring 中已有的 TencentSession，
不会显示或导出 Cookie、token、ticket、二维码 payload，也不会连接腾讯服务。

也可以直接在 GUI 的“账号管理”菜单使用“本地账号自检”写入本地 mock
TencentSession，用于验证账号表、provider 隔离和授权状态；如需复用测试 UID，
使用“清理本地自检”删除该 provider/UID 的本地 session。两项操作都不会生成
真实 QQ/微信二维码，也不会连接腾讯服务。“清理本地自检”只会清理本地自检生成的
mock TencentSession，不会删除其它已保存账号。

如果只想先查看主窗口和账号弹窗外观，可以生成本地 PNG 快照：

```powershell
qr-live-scanner-tencent gui-snapshot --provider wechat --output-dir work/gui-snapshots
qr-live-scanner-tencent gui-snapshot --provider wechat --mock-uid local-wechat-user --output-dir work/gui-snapshots
```

快照会包含 `main-window.png`、`tencent-account-dialog-<provider>.png`、
`tencent-account-import-dialog-<provider>.png` 和
`tencent-account-smoke-dialog-<provider>.png`，用于同时检查主窗口、账号二维码弹窗、
导入已保存账号弹窗和本地账号自检弹窗。

该命令会写入 `work/gui-snapshots`，只做离屏渲染，不连接直播平台、不访问
keyring，也不发送 QQ/微信真实 HTTP。带 `--mock-uid` 时只在内存里的 fake store
渲染“账号已保存”状态，不会保存真实凭证。

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
