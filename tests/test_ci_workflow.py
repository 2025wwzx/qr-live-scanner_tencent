from pathlib import Path


def test_ci_workflow_runs_cli_smoke_checks() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "qr-live-scanner-tencent gui --dry-run" in workflow
    assert "qr-live-scanner-tencent tencent-protocol-preflight" in workflow
    assert "qr-live-scanner-tencent tencent-protocol-example-check" in workflow
    assert "qr-live-scanner-tencent tencent-protocol-next-steps --provider qq" in workflow
