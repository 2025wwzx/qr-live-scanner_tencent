from pathlib import Path


def test_readme_documents_gui_mock_account_confirmation() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "GUI 本地 mock 账号测试" in readme
    assert "qr-live-scanner-tencent gui --dry-run" in readme
    assert "tencent-protocol-preflight" in readme
    assert "Git 实际忽略结果" in readme
    assert "被 git 跟踪，preflight 会失败" in readme
    assert "tencent-protocol-guide --provider qq" in readme
    assert "tencent-protocol-artifact-check" in readme
    assert "tencent-protocol-readiness" in readme
    assert "tencent-protocol-config-check" in readme
    assert "tencent-protocol-example-check" in readme
    assert "tencent-protocol-next-steps" in readme
    assert "examples/tencent-protocol-research-pack" in readme
    assert "rejects unsafe sample/config edits" in readme
    assert "rejects signed URLs or sensitive assignments" in readme
    assert "real_http=disabled" in readme
    assert "账号管理" in readme
    assert "新增账号" in readme
    assert "导入已保存账号" in readme
    assert "导入全部已保存账号" in readme
    assert "本地账号自检" in readme
    assert "清理本地自检" in readme
    assert "本地账号自检也会做账号索引验证" in readme
    assert "清理本地自检也会验证账号索引已清理" in readme
    assert "只会清理本地自检生成的" in readme
    assert "mock TencentSession" in readme
    assert "tencent-account-smoke" in readme
    assert "账号索引验证" in readme
    assert "保存 TencentSession 后会执行账号索引验证" in readme
    assert "tencent-list --provider wechat" in readme
    assert "tencent-repair-index --provider wechat" in readme
    assert "检查账号索引" in readme
    assert "索引清理验证" in readme
    assert "清理陈旧索引" in readme
    assert "安全重建该 provider 的索引" in readme
    assert "默认不会覆盖已有同 provider/UID 的" in readme
    assert "`--mock-confirm` 默认不会覆盖已有同" in readme
    assert "provider/UID 的 TencentSession" in readme
    assert "GUI 的 `Mock confirm` 也不会覆盖已有同 provider/UID 的 TencentSession" in readme
    assert "GUI 保存 TencentSession 后同样会执行账号索引验证" in readme
    assert "只检查本机 keyring 中已有的 TencentSession" in readme
    assert "导入成功前会验证本地账号索引已经包含当前 provider/UID" in readme
    assert "本地账号索引" in readme
    assert "`tencent-status` 查询后回填本地账号索引" in readme
    assert "`tencent-status` 在确认账号已保存且已授权后会验证账号索引" in readme
    assert "不会显示或导出 Cookie、token、ticket、二维码 payload" in readme
    assert "检查选中账号状态" in readme
    assert "验证登录态、授权标记和本地索引" in readme
    assert "Mock confirm" in readme
    assert "Local mock UID" in readme
    assert "QQ/微信真实 HTTP 仍然禁用" in readme
    assert "qr-live-scanner-tencent gui-snapshot" in readme
    assert "gui-snapshot --provider wechat --mock-uid" in readme
    assert "work/gui-snapshots" in readme
    assert "main-window-account-status.png" in readme
    assert "tencent-account-import-dialog" in readme
    assert "tencent-account-smoke-dialog" in readme
    assert "完整本地账号保留测试" in readme
    assert "不要加 `--cleanup`" in readme
    assert (
        "qr-live-scanner-tencent tencent-status --provider wechat --uid local-wechat-user"
        in readme
    )
    assert (
        "qr-live-scanner-tencent tencent-delete --provider wechat --uid local-wechat-user"
        in readme
    )

    provide_real_values = Path("docs/provide-real-values.md").read_text(encoding="utf-8")
    assert "tencent-protocol-artifact-check" in provide_real_values
    assert "tencent-protocol-readiness" in provide_real_values
    assert "examples/tencent-protocol-research-pack" in provide_real_values
    assert "sensitive `app_id`" in provide_real_values
    assert "`validated_protocol = true`" in provide_real_values

    protocol_research = Path("docs/tencent-protocol-research.md").read_text(
        encoding="utf-8"
    )
    assert "tencent-protocol-artifact-check" in protocol_research
    assert "tencent-protocol-readiness" in protocol_research
    assert "tencent-protocol-config-check" in protocol_research
    assert "examples/tencent-protocol-research-pack" in protocol_research
    assert "tencent-protocol-next-steps" in protocol_research
    assert "`real_http=disabled`" in protocol_research
    assert "`real_http=not-called`" in protocol_research
    assert "sensitive assignments such as `ticket=` or `Cookie:`" in protocol_research
    assert "sensitive `app_id` values" in protocol_research
    assert "sensitive endpoint path segments" in protocol_research

    example_readme = Path("examples/tencent-protocol-research-pack/README.md").read_text(
        encoding="utf-8"
    )
    assert "tencent-protocol-example-check" in example_readme
