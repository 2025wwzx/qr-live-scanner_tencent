import json

import pytest
from keyring.errors import PasswordDeleteError

from qr_live_scanner_tencent.accounts import FakeAccountStore, KeyringAccountStore, TencentSession
from qr_live_scanner_tencent.interfaces import AccountStoreError, GameID, TencentLoginProvider


def test_fake_account_store_rejects_blank_token() -> None:
    store = FakeAccountStore()

    with pytest.raises(AccountStoreError, match="token"):
        store.save_token(GameID.HONOR_OF_KINGS, "10001", "   ", authorized=True)

    assert store.get_token(GameID.HONOR_OF_KINGS, "10001") is None
    assert store.is_account_authorized("10001", GameID.HONOR_OF_KINGS) is False


def test_fake_account_store_rejects_blank_uid() -> None:
    store = FakeAccountStore()

    with pytest.raises(AccountStoreError, match="uid"):
        store.save_token(GameID.HONOR_OF_KINGS, "   ", "secret-token", authorized=True)

    assert store.tokens == {}
    assert store.authorized_accounts == set()


def test_keyring_account_store_rejects_blank_token_before_writing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str]] = []

    def fake_set_password(service: str, username: str, password: str) -> None:
        calls.append((service, username, password))

    monkeypatch.setattr(
        "qr_live_scanner_tencent.accounts.store.keyring.set_password",
        fake_set_password,
    )

    store = KeyringAccountStore()

    with pytest.raises(AccountStoreError, match="token"):
        store.save_token(GameID.HONOR_OF_KINGS, "10001", "", authorized=True)

    assert calls == []


def test_keyring_account_store_rejects_blank_uid_before_writing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str]] = []

    def fake_set_password(service: str, username: str, password: str) -> None:
        calls.append((service, username, password))

    monkeypatch.setattr(
        "qr_live_scanner_tencent.accounts.store.keyring.set_password",
        fake_set_password,
    )

    store = KeyringAccountStore()

    with pytest.raises(AccountStoreError, match="uid"):
        store.save_token(GameID.HONOR_OF_KINGS, "", "secret-token", authorized=True)

    assert calls == []


def test_keyring_account_store_deletes_token_and_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    def fake_delete_password(service: str, username: str) -> None:
        calls.append((service, username))

    monkeypatch.setattr(
        "qr_live_scanner_tencent.accounts.store.keyring.delete_password",
        fake_delete_password,
    )

    store = KeyringAccountStore()

    store.delete_token(GameID.HONOR_OF_KINGS, "10001")

    assert calls == [
        ("qr-live-scanner-tencent", "honor_of_kings:10001"),
        ("qr-live-scanner-tencent", "authorized:honor_of_kings:10001"),
    ]


def test_keyring_account_store_ignores_missing_values_during_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    def fake_delete_password(service: str, username: str) -> None:
        calls.append((service, username))
        if username == "honor_of_kings:10001":
            raise PasswordDeleteError("missing token")

    monkeypatch.setattr(
        "qr_live_scanner_tencent.accounts.store.keyring.delete_password",
        fake_delete_password,
    )

    store = KeyringAccountStore()

    store.delete_token(GameID.HONOR_OF_KINGS, "10001")

    assert calls == [
        ("qr-live-scanner-tencent", "honor_of_kings:10001"),
        ("qr-live-scanner-tencent", "authorized:honor_of_kings:10001"),
    ]


def test_fake_account_store_manages_tencent_session_separately_from_game_tokens() -> None:
    store = FakeAccountStore()
    session = TencentSession(
        uid="tencent-uid",
        provider=TencentLoginProvider.QQ,
        credentials={"access_token": "SECRET_ACCESS_TOKEN", "openid": "SECRET_OPENID"},
    )

    store.save_tencent_session(session, authorized=True)

    assert store.get_tencent_session("tencent-uid") == session
    assert store.get_token(GameID.HONOR_OF_KINGS, "tencent-uid") is None
    assert store.is_tencent_authorized("tencent-uid") is True

    store.delete_tencent_session("tencent-uid")

    assert store.get_tencent_session("tencent-uid") is None
    assert store.is_tencent_authorized("tencent-uid") is False


def test_fake_account_store_lists_tencent_sessions_by_provider_without_credentials() -> None:
    store = FakeAccountStore()
    store.save_tencent_session(
        TencentSession(
            uid="qq-user",
            provider=TencentLoginProvider.QQ,
            credentials={"access_token": "SECRET_ACCESS_TOKEN"},
        ),
        authorized=True,
    )
    store.save_tencent_session(
        TencentSession(
            uid="wechat-user",
            provider=TencentLoginProvider.WECHAT,
            credentials={"access_token": "SECRET_WECHAT_TOKEN"},
        ),
        authorized=False,
    )

    entries = store.list_tencent_sessions(TencentLoginProvider.QQ)

    assert len(entries) == 1
    assert entries[0].uid == "qq-user"
    assert entries[0].provider is TencentLoginProvider.QQ
    assert entries[0].authorized is True
    assert not hasattr(entries[0], "credentials")


def test_keyring_account_store_saves_tencent_session_under_tencent_namespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str]] = []

    def fake_get_password(service: str, username: str) -> str | None:
        assert service == "qr-live-scanner-tencent"
        assert username == "tencent:index:qq"
        return None

    def fake_set_password(service: str, username: str, password: str) -> None:
        calls.append((service, username, password))

    monkeypatch.setattr(
        "qr_live_scanner_tencent.accounts.store.keyring.get_password",
        fake_get_password,
    )
    monkeypatch.setattr(
        "qr_live_scanner_tencent.accounts.store.keyring.set_password",
        fake_set_password,
    )
    store = KeyringAccountStore()
    session = TencentSession(
        uid="tencent-uid",
        provider=TencentLoginProvider.QQ,
        credentials={"access_token": "SECRET_ACCESS_TOKEN", "openid": "SECRET_OPENID"},
    )

    store.save_tencent_session(session, authorized=True)

    assert calls[0][0] == "qr-live-scanner-tencent"
    assert calls[0][1] == "tencent:qq:tencent-uid"
    assert json.loads(calls[0][2]) == {
        "uid": "tencent-uid",
        "provider": "qq",
        "credentials": {
            "access_token": "SECRET_ACCESS_TOKEN",
            "openid": "SECRET_OPENID",
        },
    }
    assert calls[1] == ("qr-live-scanner-tencent", "authorized:tencent:qq:tencent-uid", "1")


def test_keyring_account_store_saves_tencent_account_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str]] = []

    def fake_get_password(service: str, username: str) -> str | None:
        assert service == "qr-live-scanner-tencent"
        assert username == "tencent:index:qq"
        return None

    def fake_set_password(service: str, username: str, password: str) -> None:
        calls.append((service, username, password))

    monkeypatch.setattr(
        "qr_live_scanner_tencent.accounts.store.keyring.get_password",
        fake_get_password,
    )
    monkeypatch.setattr(
        "qr_live_scanner_tencent.accounts.store.keyring.set_password",
        fake_set_password,
    )
    store = KeyringAccountStore()
    session = TencentSession(
        uid="tencent-uid",
        provider=TencentLoginProvider.QQ,
        credentials={"access_token": "SECRET_ACCESS_TOKEN"},
    )

    store.save_tencent_session(session, authorized=True)

    assert calls[2][0] == "qr-live-scanner-tencent"
    assert calls[2][1] == "tencent:index:qq"
    assert json.loads(calls[2][2]) == [{"authorized": True, "provider": "qq", "uid": "tencent-uid"}]
    assert "SECRET_ACCESS_TOKEN" not in calls[2][2]


def test_keyring_account_store_deletes_tencent_session_without_touching_game_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    set_calls: list[tuple[str, str, str]] = []

    def fake_get_password(service: str, username: str) -> str | None:
        assert service == "qr-live-scanner-tencent"
        assert username == "tencent:index:qq"
        return None

    def fake_set_password(service: str, username: str, password: str) -> None:
        set_calls.append((service, username, password))

    def fake_delete_password(service: str, username: str) -> None:
        calls.append((service, username))

    monkeypatch.setattr(
        "qr_live_scanner_tencent.accounts.store.keyring.get_password",
        fake_get_password,
    )
    monkeypatch.setattr(
        "qr_live_scanner_tencent.accounts.store.keyring.set_password",
        fake_set_password,
    )
    monkeypatch.setattr(
        "qr_live_scanner_tencent.accounts.store.keyring.delete_password",
        fake_delete_password,
    )
    store = KeyringAccountStore()

    store.delete_tencent_session("tencent-uid")

    assert calls == [
        ("qr-live-scanner-tencent", "tencent:qq:tencent-uid"),
        ("qr-live-scanner-tencent", "authorized:tencent:qq:tencent-uid"),
    ]
    assert set_calls == [("qr-live-scanner-tencent", "tencent:index:qq", "[]")]


def test_keyring_account_store_deletes_tencent_account_index_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delete_calls: list[tuple[str, str]] = []
    set_calls: list[tuple[str, str, str]] = []

    def fake_get_password(service: str, username: str) -> str | None:
        assert service == "qr-live-scanner-tencent"
        assert username == "tencent:index:wechat"
        return json.dumps(
            [
                {"uid": "keep-user", "provider": "wechat", "authorized": True},
                {"uid": "delete-user", "provider": "wechat", "authorized": True},
            ]
        )

    def fake_set_password(service: str, username: str, password: str) -> None:
        set_calls.append((service, username, password))

    def fake_delete_password(service: str, username: str) -> None:
        delete_calls.append((service, username))

    monkeypatch.setattr(
        "qr_live_scanner_tencent.accounts.store.keyring.get_password",
        fake_get_password,
    )
    monkeypatch.setattr(
        "qr_live_scanner_tencent.accounts.store.keyring.set_password",
        fake_set_password,
    )
    monkeypatch.setattr(
        "qr_live_scanner_tencent.accounts.store.keyring.delete_password",
        fake_delete_password,
    )
    store = KeyringAccountStore()

    store.delete_tencent_session("delete-user", TencentLoginProvider.WECHAT)

    assert delete_calls == [
        ("qr-live-scanner-tencent", "tencent:wechat:delete-user"),
        ("qr-live-scanner-tencent", "authorized:tencent:wechat:delete-user"),
    ]
    assert set_calls == [
        (
            "qr-live-scanner-tencent",
            "tencent:index:wechat",
            json.dumps(
                [{"authorized": True, "provider": "wechat", "uid": "keep-user"}],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    ]
