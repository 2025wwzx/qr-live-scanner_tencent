from pathlib import Path


def test_readme_documents_gui_mock_account_confirmation() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "GUI 本地 mock 账号测试" in readme
    assert "qr-live-scanner-tencent gui --dry-run" in readme
    assert "账号管理" in readme
    assert "新增账号" in readme
    assert "Mock confirm" in readme
    assert "Local mock UID" in readme
    assert "QQ/微信真实 HTTP 仍然禁用" in readme
