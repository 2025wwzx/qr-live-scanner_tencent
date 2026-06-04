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

## License

CC BY-NC 4.0。禁止任何形式的商业用途或贩卖。
