from pathlib import Path

import pytest

import qr_live_scanner_tencent.__main__ as main_module
from qr_live_scanner_tencent.__main__ import main
from qr_live_scanner_tencent.accounts import TencentSession
from qr_live_scanner_tencent.accounts.tencent_qr_login import (
    TencentAccountQRLoginError,
    TencentAccountQRLoginState,
    TencentAccountQRLoginStatus,
    TencentAccountQRTicket,
)
from qr_live_scanner_tencent.interfaces import AccountStoreError, TencentLoginProvider


def test_tencent_login_cli_dry_run_writes_demo_qr_without_echoing_secrets(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "tencent-login.png"

    exit_code = main(
        [
            "tencent-login",
            "--provider",
            "qq",
            "--dry-run",
            "--qr-output",
            str(output_path),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert output_path.exists()
    assert output_path.read_bytes().startswith(b"\x89PNG")
    assert "Tencent account QR dry-run image written" in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "ticket" not in output.lower()
    assert "payload" not in output.lower()


def test_tencent_login_cli_mock_confirm_saves_local_session_without_http(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "wechat-login.png"
    saved: list[tuple[TencentSession, bool]] = []

    class FakeStore:
        def get_tencent_session(
            self,
            uid: str,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> TencentSession | None:
            assert uid == "local-wechat-user"
            assert provider is TencentLoginProvider.WECHAT
            return None

        def save_tencent_session(self, session: object, *, authorized: bool) -> None:
            assert isinstance(session, TencentSession)
            saved.append((session, authorized))

    def fail_if_real_service_is_created(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("mock confirm must not create the real QR login service")

    monkeypatch.setattr(main_module, "KeyringAccountStore", FakeStore)
    monkeypatch.setattr(
        main_module,
        "_new_tencent_account_qr_login_service",
        fail_if_real_service_is_created,
    )

    exit_code = _run_main(
        [
            "tencent-login",
            "--provider",
            "wechat",
            "--mock-confirm",
            "--mock-uid",
            "local-wechat-user",
            "--qr-output",
            str(output_path),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert output_path.exists()
    assert output_path.read_bytes().startswith(b"\x89PNG")
    assert len(saved) == 1
    session, authorized = saved[0]
    assert session.uid == "local-wechat-user"
    assert session.provider is TencentLoginProvider.WECHAT
    assert session.credentials == {"mock_session": "local-mock-only"}
    assert authorized is True
    assert "mock Tencent account session saved" in output
    assert "local-wechat-user" not in output
    assert "local-mock-only" not in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "ticket" not in output.lower()
    assert "payload" not in output.lower()


def test_tencent_login_cli_mock_confirm_does_not_overwrite_existing_session(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "existing-qq-login.png"
    operations: list[str] = []

    class FakeStore:
        def get_tencent_session(
            self,
            uid: str,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> TencentSession | None:
            assert uid == "existing-qq-user"
            assert provider is TencentLoginProvider.QQ
            operations.append("get")
            return TencentSession(
                uid=uid,
                provider=provider,
                credentials={"access_token": "SECRET_ACCESS_TOKEN"},
            )

        def save_tencent_session(self, session: object, *, authorized: bool) -> None:
            raise AssertionError("existing Tencent session must not be overwritten")

    monkeypatch.setattr(main_module, "KeyringAccountStore", FakeStore)

    exit_code = _run_main(
        [
            "tencent-login",
            "--provider",
            "qq",
            "--mock-confirm",
            "--mock-uid",
            "existing-qq-user",
            "--qr-output",
            str(output_path),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 1
    assert operations == ["get"]
    assert not output_path.exists()
    assert "already exists" in output
    assert "SECRET_ACCESS_TOKEN" not in output
    assert "existing-qq-user" not in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "ticket" not in output.lower()
    assert "payload" not in output.lower()


def test_tencent_login_cli_mock_confirm_removes_qr_when_save_fails(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "failed-save-login.png"

    class FailingStore:
        def get_tencent_session(
            self,
            uid: str,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> TencentSession | None:
            assert uid == "local-qq-user"
            assert provider is TencentLoginProvider.QQ
            return None

        def save_tencent_session(self, session: object, *, authorized: bool) -> None:
            assert isinstance(session, TencentSession)
            assert authorized is True
            raise AccountStoreError("SECRET_ACCESS_TOKEN should not be visible")

    monkeypatch.setattr(main_module, "KeyringAccountStore", FailingStore)

    exit_code = _run_main(
        [
            "tencent-login",
            "--provider",
            "qq",
            "--mock-confirm",
            "--mock-uid",
            "local-qq-user",
            "--qr-output",
            str(output_path),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert output_path.exists() is False
    assert "credential storage unavailable" in output
    assert "SECRET_ACCESS_TOKEN" not in output
    assert "local-qq-user" not in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "ticket" not in output.lower()
    assert "payload" not in output.lower()


def test_tencent_login_cli_mock_confirm_requires_mock_uid_before_writing_qr(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "mock-login.png"

    def fail_if_real_service_is_created(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("mock confirm must validate before creating services")

    monkeypatch.setattr(
        main_module,
        "_new_tencent_account_qr_login_service",
        fail_if_real_service_is_created,
    )

    exit_code = _run_main(
        [
            "tencent-login",
            "--provider",
            "qq",
            "--mock-confirm",
            "--qr-output",
            str(output_path),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert not output_path.exists()
    assert "mock uid is required" in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()


@pytest.mark.parametrize(
    ("option", "value", "message"),
    [
        ("--timeout-seconds", "0", "timeout seconds must be finite and positive"),
        ("--poll-interval-seconds", "0", "poll interval seconds must be finite and positive"),
    ],
)
def test_tencent_login_cli_validates_timing_before_creating_runtime_resources(
    option: str,
    value: str,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    service_requests: list[TencentLoginProvider] = []

    def fake_service_factory(provider: TencentLoginProvider) -> object:
        service_requests.append(provider)
        raise AssertionError("service should not be created")

    monkeypatch.setattr(main_module, "_new_tencent_account_qr_login_service", fake_service_factory)

    exit_code = main(
        [
            "tencent-login",
            "--provider",
            "qq",
            "--qr-output",
            str(tmp_path / "tencent-login.png"),
            option,
            value,
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert message in output
    assert service_requests == []


def test_tencent_login_cli_uses_local_protocol_config(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "tencent-account-login.toml"
    config_path.write_text(
        "\n".join(
            [
                "[account_qr_login.qq]",
                "validated_protocol = true",
                'fetch_url = "https://example.test/qq/fetch"',
                'query_url = "https://example.test/qq/query"',
                'app_id = "test-app"',
            ]
        ),
        encoding="utf-8",
    )
    captured_configs: list[tuple[bool, str, str, str]] = []

    async def fake_capture(
        service: object,
        *,
        qr_output_path: Path,
        timeout_seconds: float,
        poll_interval_seconds: float,
    ) -> TencentSession:
        assert qr_output_path == tmp_path / "tencent-login.png"
        assert timeout_seconds == 3
        assert poll_interval_seconds == 0.01
        assert hasattr(service, "config")
        config = service.config
        captured_configs.append(
            (
                bool(config.validated_protocol),
                str(config.fetch_url),
                str(config.query_url),
                str(config.app_id),
            )
        )
        return TencentSession(
            uid="10001",
            provider=TencentLoginProvider.QQ,
            credentials={"access_token": "SECRET_ACCESS_TOKEN", "openid": "SECRET_OPENID"},
        )

    class FakeStore:
        def save_tencent_session(self, session: object, *, authorized: bool) -> None:
            assert isinstance(session, TencentSession)
            assert authorized is True

    monkeypatch.setattr(main_module, "_capture_tencent_session_from_qr", fake_capture)
    monkeypatch.setattr(main_module, "KeyringAccountStore", FakeStore)

    exit_code = main(
        [
            "tencent-login",
            "--provider",
            "qq",
            "--protocol-config",
            str(config_path),
            "--qr-output",
            str(tmp_path / "tencent-login.png"),
            "--timeout-seconds",
            "3",
            "--poll-interval-seconds",
            "0.01",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert captured_configs == [
        (
            True,
            "https://example.test/qq/fetch",
            "https://example.test/qq/query",
            "test-app",
        )
    ]
    assert "Tencent account session saved" in output
    assert "SECRET_ACCESS_TOKEN" not in output
    assert "10001" not in output


def test_tencent_login_cli_rejects_mismatched_provider_session(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    saved: list[TencentSession] = []

    async def fake_capture(
        service: object,
        *,
        qr_output_path: Path,
        timeout_seconds: float,
        poll_interval_seconds: float,
    ) -> TencentSession:
        assert service is not None
        assert qr_output_path == tmp_path / "tencent-login.png"
        assert timeout_seconds == 3
        assert poll_interval_seconds == 0.01
        return TencentSession(
            uid="wechat-user",
            provider=TencentLoginProvider.QQ,
            credentials={"access_token": "SECRET_ACCESS_TOKEN", "openid": "SECRET_OPENID"},
        )

    class FakeStore:
        def save_tencent_session(self, session: object, *, authorized: bool) -> None:
            assert authorized is True
            assert isinstance(session, TencentSession)
            saved.append(session)

    monkeypatch.setattr(main_module, "_capture_tencent_session_from_qr", fake_capture)
    monkeypatch.setattr(
        main_module,
        "_new_tencent_account_qr_login_service",
        lambda _provider, **_kwargs: object(),
    )
    monkeypatch.setattr(main_module, "KeyringAccountStore", FakeStore)

    exit_code = main(
        [
            "tencent-login",
            "--provider",
            "wechat",
            "--qr-output",
            str(tmp_path / "tencent-login.png"),
            "--timeout-seconds",
            "3",
            "--poll-interval-seconds",
            "0.01",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert saved == []
    assert "provider mismatch" in output
    assert "wechat-user" not in output
    assert "SECRET_ACCESS_TOKEN" not in output
    assert "SECRET_OPENID" not in output


def test_tencent_login_cli_scans_qr_and_saves_without_echoing_secrets(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    saved: list[tuple[TencentSession, bool]] = []
    rendered: list[tuple[str, Path]] = []
    closed: list[bool] = []

    class FakeService:
        def __init__(self, **_kwargs: object) -> None:
            self.statuses = [
                TencentAccountQRLoginStatus(
                    provider=TencentLoginProvider.QQ,
                    state=TencentAccountQRLoginState.WAITING,
                ),
                TencentAccountQRLoginStatus(
                    provider=TencentLoginProvider.QQ,
                    state=TencentAccountQRLoginState.SCANNED,
                ),
                TencentAccountQRLoginStatus(
                    provider=TencentLoginProvider.QQ,
                    state=TencentAccountQRLoginState.CONFIRMED,
                    session=TencentSession(
                        uid="10001",
                        provider=TencentLoginProvider.QQ,
                        credentials={
                            "access_token": "SECRET_ACCESS_TOKEN",
                            "openid": "SECRET_OPENID",
                        },
                    ),
                ),
            ]

        async def fetch_qr(self) -> TencentAccountQRTicket:
            return TencentAccountQRTicket(
                provider=TencentLoginProvider.QQ,
                app_id="test-app",
                ticket="SECRET_TICKET",
                qr_url="https://example.test/qq/qr?ticket=SECRET_TICKET",
                device_id="0123456789abcdef0123456789abcdef",
            )

        async def query_qr(self, ticket: object) -> TencentAccountQRLoginStatus:
            assert isinstance(ticket, TencentAccountQRTicket)
            return self.statuses.pop(0)

        def save_confirmed_session(
            self,
            status: TencentAccountQRLoginStatus,
            account_store: object,
        ) -> TencentSession:
            assert status.session is not None
            assert isinstance(account_store, FakeStore)
            account_store.save_tencent_session(status.session, authorized=True)
            return status.session

        def write_qr_png(self, payload_ticket: TencentAccountQRTicket, output_path: Path) -> None:
            rendered.append((payload_ticket.qr_url, output_path))
            output_path.write_bytes(b"PNG")

        async def aclose(self) -> None:
            closed.append(True)

    class FakeStore:
        def save_tencent_session(self, session: object, *, authorized: bool) -> None:
            assert isinstance(session, TencentSession)
            saved.append((session, authorized))

    monkeypatch.setattr(
        main_module,
        "_new_tencent_account_qr_login_service",
        lambda _provider, **_kwargs: FakeService(),
    )
    monkeypatch.setattr(main_module, "KeyringAccountStore", FakeStore)

    exit_code = main(
        [
            "tencent-login",
            "--provider",
            "qq",
            "--qr-output",
            str(tmp_path / "tencent-login.png"),
            "--timeout-seconds",
            "3",
            "--poll-interval-seconds",
            "0.01",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert saved and saved[0][1] is True
    assert rendered == [
        (
            "https://example.test/qq/qr?ticket=SECRET_TICKET",
            tmp_path / "tencent-login.png",
        )
    ]
    assert closed == [True]
    assert not (tmp_path / "tencent-login.png").exists()
    assert "Tencent account QR image written" in output
    assert "Tencent account session saved" in output
    assert "10001" not in output
    assert "SECRET_TICKET" not in output
    assert "SECRET_ACCESS_TOKEN" not in output
    assert "SECRET_OPENID" not in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()


def test_tencent_login_cli_redacts_storage_errors_after_confirmed_session(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    closed: list[bool] = []

    class FakeService:
        async def fetch_qr(self) -> TencentAccountQRTicket:
            return TencentAccountQRTicket(
                provider=TencentLoginProvider.QQ,
                app_id="test-app",
                ticket="SECRET_TICKET",
                qr_url="https://example.test/qq/qr?ticket=SECRET_TICKET",
                device_id="0123456789abcdef0123456789abcdef",
            )

        async def query_qr(self, ticket: object) -> TencentAccountQRLoginStatus:
            assert isinstance(ticket, TencentAccountQRTicket)
            return TencentAccountQRLoginStatus(
                provider=TencentLoginProvider.QQ,
                state=TencentAccountQRLoginState.CONFIRMED,
                session=TencentSession(
                    uid="10001",
                    provider=TencentLoginProvider.QQ,
                    credentials={
                        "access_token": "SECRET_ACCESS_TOKEN",
                        "openid": "SECRET_OPENID",
                    },
                ),
            )

        def write_qr_png(self, payload_ticket: TencentAccountQRTicket, output_path: Path) -> None:
            assert payload_ticket.ticket == "SECRET_TICKET"
            output_path.write_bytes(b"PNG")

        async def aclose(self) -> None:
            closed.append(True)

    class FailingStore:
        def save_tencent_session(self, session: object, *, authorized: bool) -> None:
            assert isinstance(session, TencentSession)
            assert authorized is True
            raise AccountStoreError("SECRET_ACCESS_TOKEN should not be visible")

    monkeypatch.setattr(
        main_module,
        "_new_tencent_account_qr_login_service",
        lambda _provider, **_kwargs: FakeService(),
    )
    monkeypatch.setattr(main_module, "KeyringAccountStore", FailingStore)

    exit_code = main(
        [
            "tencent-login",
            "--provider",
            "qq",
            "--qr-output",
            str(tmp_path / "tencent-login.png"),
            "--timeout-seconds",
            "3",
            "--poll-interval-seconds",
            "0.01",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert closed == [True]
    assert "credential storage unavailable" in output
    assert "SECRET_TICKET" not in output
    assert "SECRET_ACCESS_TOKEN" not in output
    assert "SECRET_OPENID" not in output
    assert "10001" not in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()


def test_tencent_status_cli_reports_saved_authorized_without_echoing_secrets(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeStore:
        def get_tencent_session(
            self,
            uid: str,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> TencentSession | None:
            assert uid == "10001"
            assert provider is TencentLoginProvider.QQ
            return TencentSession(
                uid=uid,
                provider=provider,
                credentials={"access_token": "SECRET_ACCESS_TOKEN", "openid": "SECRET_OPENID"},
            )

        def is_tencent_authorized(
            self,
            uid: str,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> bool:
            assert uid == "10001"
            assert provider is TencentLoginProvider.QQ
            return True

    monkeypatch.setattr(main_module, "KeyringAccountStore", FakeStore)

    exit_code = main(["tencent-status", "--provider", "qq", "--uid", "10001"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "saved and authorized" in output
    assert "10001" not in output
    assert "SECRET_ACCESS_TOKEN" not in output
    assert "SECRET_OPENID" not in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()


def test_tencent_status_cli_redacts_storage_errors(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeStore:
        def get_tencent_session(
            self,
            uid: str,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> TencentSession | None:
            raise AccountStoreError("SECRET_ACCESS_TOKEN should not be visible")

        def is_tencent_authorized(
            self,
            uid: str,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> bool:
            raise AssertionError("authorization should not run after storage failure")

    monkeypatch.setattr(main_module, "KeyringAccountStore", FakeStore)

    exit_code = main(["tencent-status", "--provider", "qq", "--uid", "10001"])
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "credential storage unavailable" in output
    assert "SECRET_ACCESS_TOKEN" not in output
    assert "10001" not in output


def test_tencent_delete_cli_removes_saved_session_without_echoing_identifiers(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deleted: list[tuple[str, TencentLoginProvider]] = []

    class FakeStore:
        def delete_tencent_session(
            self,
            uid: str,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> None:
            deleted.append((uid, provider))

    monkeypatch.setattr(main_module, "KeyringAccountStore", FakeStore)

    exit_code = main(["tencent-delete", "--provider", "wechat", "--uid", "local-wechat-user"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert deleted == [("local-wechat-user", TencentLoginProvider.WECHAT)]
    assert "Tencent account session deleted" in output
    assert "local-wechat-user" not in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "ticket" not in output.lower()
    assert "payload" not in output.lower()


def test_tencent_delete_cli_redacts_storage_errors(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeStore:
        def delete_tencent_session(
            self,
            uid: str,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> None:
            assert uid == "10001"
            assert provider is TencentLoginProvider.QQ
            raise AccountStoreError("SECRET_ACCESS_TOKEN should not be visible")

    monkeypatch.setattr(main_module, "KeyringAccountStore", FakeStore)

    exit_code = main(["tencent-delete", "--provider", "qq", "--uid", "10001"])
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "credential storage unavailable" in output
    assert "SECRET_ACCESS_TOKEN" not in output
    assert "10001" not in output


def test_tencent_account_smoke_cli_saves_verifies_and_cleans_up_without_http(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    operations: list[str] = []

    class FakeStore:
        def __init__(self) -> None:
            self.sessions: dict[tuple[TencentLoginProvider, str], TencentSession] = {}
            self.authorized: set[tuple[TencentLoginProvider, str]] = set()

        def save_tencent_session(self, session: object, *, authorized: bool) -> None:
            assert isinstance(session, TencentSession)
            assert session.provider is TencentLoginProvider.WECHAT
            assert session.uid == "local-wechat-user"
            assert session.credentials == {"mock_session": "local-smoke-only"}
            operations.append("save")
            key = (session.provider, session.uid)
            self.sessions[key] = session
            if authorized:
                self.authorized.add(key)

        def get_tencent_session(
            self,
            uid: str,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> TencentSession | None:
            assert uid == "local-wechat-user"
            assert provider is TencentLoginProvider.WECHAT
            operations.append("get")
            return self.sessions.get((provider, uid))

        def is_tencent_authorized(
            self,
            uid: str,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> bool:
            assert uid == "local-wechat-user"
            assert provider is TencentLoginProvider.WECHAT
            operations.append("authorized")
            return (provider, uid) in self.authorized

        def delete_tencent_session(
            self,
            uid: str,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> None:
            assert uid == "local-wechat-user"
            assert provider is TencentLoginProvider.WECHAT
            operations.append("delete")
            self.sessions.pop((provider, uid), None)
            self.authorized.discard((provider, uid))

    fake_store = FakeStore()

    def fail_if_real_service_is_created(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("local account smoke must not create a real QR login service")

    monkeypatch.setattr(main_module, "KeyringAccountStore", lambda: fake_store)
    monkeypatch.setattr(
        main_module,
        "_new_tencent_account_qr_login_service",
        fail_if_real_service_is_created,
    )

    exit_code = main(
        [
            "tencent-account-smoke",
            "--provider",
            "wechat",
            "--uid",
            "local-wechat-user",
            "--cleanup",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert operations == ["get", "save", "get", "authorized", "delete"]
    assert fake_store.sessions == {}
    assert fake_store.authorized == set()
    assert "Tencent account local smoke passed" in output
    assert "Tencent account local smoke cleaned up" in output
    assert "local-wechat-user" not in output
    assert "local-smoke-only" not in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()
    assert "ticket" not in output.lower()
    assert "payload" not in output.lower()


def test_tencent_account_smoke_cli_redacts_storage_errors(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeStore:
        def get_tencent_session(
            self,
            uid: str,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> TencentSession | None:
            assert uid == "10001"
            assert provider is TencentLoginProvider.QQ
            raise AccountStoreError("SECRET_ACCESS_TOKEN should not be visible")

        def save_tencent_session(self, session: object, *, authorized: bool) -> None:
            raise AssertionError("save should not run after preflight storage failure")

    monkeypatch.setattr(main_module, "KeyringAccountStore", FakeStore)

    exit_code = main(["tencent-account-smoke", "--provider", "qq", "--uid", "10001"])
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "credential storage unavailable" in output
    assert "SECRET_ACCESS_TOKEN" not in output
    assert "10001" not in output


def test_tencent_account_smoke_cli_does_not_overwrite_existing_session(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    operations: list[str] = []

    class FakeStore:
        def get_tencent_session(
            self,
            uid: str,
            provider: TencentLoginProvider = TencentLoginProvider.QQ,
        ) -> TencentSession | None:
            assert uid == "10001"
            assert provider is TencentLoginProvider.QQ
            operations.append("get")
            return TencentSession(
                uid=uid,
                provider=provider,
                credentials={"access_token": "SECRET_ACCESS_TOKEN"},
            )

        def save_tencent_session(self, session: object, *, authorized: bool) -> None:
            raise AssertionError("existing Tencent session must not be overwritten")

    monkeypatch.setattr(main_module, "KeyringAccountStore", FakeStore)

    exit_code = main(["tencent-account-smoke", "--provider", "qq", "--uid", "10001"])
    output = capsys.readouterr().out

    assert exit_code == 1
    assert operations == ["get"]
    assert "already exists" in output
    assert "SECRET_ACCESS_TOKEN" not in output
    assert "10001" not in output
    assert "token" not in output.lower()
    assert "cookie" not in output.lower()


async def test_tencent_login_cli_closes_service_when_qr_fetch_fails(tmp_path: Path) -> None:
    closed: list[bool] = []

    class FailingService:
        async def fetch_qr(self) -> TencentAccountQRTicket:
            raise TencentAccountQRLoginError("Tencent account QR fetch HTTP failed")

        async def query_qr(self, ticket: TencentAccountQRTicket) -> TencentAccountQRLoginStatus:
            raise AssertionError("query should not run after fetch failure")

        def write_qr_png(self, ticket: TencentAccountQRTicket, output_path: Path) -> None:
            raise AssertionError("QR should not render after fetch failure")

        async def aclose(self) -> None:
            closed.append(True)

    with pytest.raises(TencentAccountQRLoginError, match="fetch HTTP failed"):
        await main_module._capture_tencent_session_from_qr(
            FailingService(),
            qr_output_path=tmp_path / "tencent-login.png",
            timeout_seconds=1.0,
            poll_interval_seconds=0.01,
        )

    assert closed == [True]


def _run_main(argv: list[str]) -> int:
    try:
        return main(argv)
    except SystemExit as exc:
        if isinstance(exc.code, int):
            return exc.code
        return 1
