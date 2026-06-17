from pathlib import Path


def test_readme_documents_gui_mock_account_confirmation() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "GUI 本地 mock 账号测试" in readme
    assert "qr-live-scanner-tencent gui --dry-run" in readme
    assert "账号管理" in readme
    assert "新增账号" in readme
    assert "导入已保存账号" in readme
    assert "本地账号自检" in readme
    assert "清理本地自检" in readme
    assert "只会清理本地自检生成的" in readme
    assert "mock TencentSession" in readme
    assert "tencent-account-smoke" in readme
    assert "默认不会覆盖已有同 provider/UID 的" in readme
    assert "只检查本机 keyring 中已有的 TencentSession" in readme
    assert "不会显示或导出 Cookie、token、ticket、二维码 payload" in readme
    assert "Mock confirm" in readme
    assert "Local mock UID" in readme
    assert "QQ/微信真实 HTTP 仍然禁用" in readme
    assert "qr-live-scanner-tencent gui-snapshot" in readme
    assert "gui-snapshot --provider wechat --mock-uid" in readme
    assert "work/gui-snapshots" in readme
