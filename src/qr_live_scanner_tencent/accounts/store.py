from __future__ import annotations

from dataclasses import dataclass, field

import keyring
from keyring.errors import KeyringError, NoKeyringError, PasswordDeleteError

from qr_live_scanner_tencent.accounts.session import (
    TencentSession,
    dump_tencent_session,
    load_tencent_session,
)
from qr_live_scanner_tencent.config import AccountConfig
from qr_live_scanner_tencent.interfaces import AccountStoreError, GameID, TencentLoginProvider

NO_KEYRING_MESSAGE = (
    "No usable keyring backend. Use FakeAccountStore in tests or configure OS keyring."
)


@dataclass(slots=True)
class FakeAccountStore:
    tokens: dict[tuple[GameID, str], str] = field(default_factory=dict)
    authorized_accounts: set[tuple[GameID, str]] = field(default_factory=set)
    tencent_sessions: dict[tuple[TencentLoginProvider, str], TencentSession] = field(
        default_factory=dict
    )
    authorized_tencent_accounts: set[tuple[TencentLoginProvider, str]] = field(default_factory=set)

    def get_token(self, game_id: GameID, uid: str) -> str | None:
        uid = _require_uid(uid)
        return self.tokens.get((game_id, uid))

    def save_token(self, game_id: GameID, uid: str, token: str, *, authorized: bool) -> None:
        uid = _require_uid(uid)
        token = _require_token(token)
        key = (game_id, uid)
        self.tokens[key] = token
        if authorized:
            self.authorized_accounts.add(key)
        else:
            self.authorized_accounts.discard(key)

    def delete_token(self, game_id: GameID, uid: str) -> None:
        uid = _require_uid(uid)
        key = (game_id, uid)
        self.tokens.pop(key, None)
        self.authorized_accounts.discard(key)

    def is_account_authorized(self, uid: str, game_id: GameID) -> bool:
        uid = _require_uid(uid)
        return (game_id, uid) in self.authorized_accounts

    def get_tencent_session(
        self,
        uid: str,
        provider: TencentLoginProvider = TencentLoginProvider.QQ,
    ) -> TencentSession | None:
        uid = _require_uid(uid)
        provider = _provider(provider)
        return self.tencent_sessions.get((provider, uid))

    def save_tencent_session(self, session: object, *, authorized: bool) -> None:
        session = _require_tencent_session(session)
        uid = _require_uid(session.uid)
        key = (session.provider, uid)
        self.tencent_sessions[key] = session
        if authorized:
            self.authorized_tencent_accounts.add(key)
        else:
            self.authorized_tencent_accounts.discard(key)

    def delete_tencent_session(
        self,
        uid: str,
        provider: TencentLoginProvider = TencentLoginProvider.QQ,
    ) -> None:
        uid = _require_uid(uid)
        provider = _provider(provider)
        key = (provider, uid)
        self.tencent_sessions.pop(key, None)
        self.authorized_tencent_accounts.discard(key)

    def is_tencent_authorized(
        self,
        uid: str,
        provider: TencentLoginProvider = TencentLoginProvider.QQ,
    ) -> bool:
        uid = _require_uid(uid)
        provider = _provider(provider)
        return (provider, uid) in self.authorized_tencent_accounts


@dataclass(slots=True)
class KeyringAccountStore:
    config: AccountConfig = field(default_factory=AccountConfig)

    def get_token(self, game_id: GameID, uid: str) -> str | None:
        uid = _require_uid(uid)
        try:
            return keyring.get_password(
                self.config.keyring_service, self.config.keyring_username(game_id, uid)
            )
        except (NoKeyringError, KeyringError) as exc:
            raise AccountStoreError(NO_KEYRING_MESSAGE) from exc

    def save_token(self, game_id: GameID, uid: str, token: str, *, authorized: bool) -> None:
        uid = _require_uid(uid)
        token = _require_token(token)
        try:
            keyring.set_password(
                self.config.keyring_service, self.config.keyring_username(game_id, uid), token
            )
            keyring.set_password(
                self.config.keyring_service,
                self._authorization_username(game_id, uid),
                "1" if authorized else "0",
            )
        except (NoKeyringError, KeyringError) as exc:
            raise AccountStoreError(NO_KEYRING_MESSAGE) from exc

    def delete_token(self, game_id: GameID, uid: str) -> None:
        uid = _require_uid(uid)
        try:
            self._delete_password(self.config.keyring_username(game_id, uid))
            self._delete_password(self._authorization_username(game_id, uid))
        except (NoKeyringError, KeyringError) as exc:
            raise AccountStoreError(NO_KEYRING_MESSAGE) from exc

    def is_account_authorized(self, uid: str, game_id: GameID) -> bool:
        uid = _require_uid(uid)
        try:
            value = keyring.get_password(
                self.config.keyring_service, self._authorization_username(game_id, uid)
            )
        except (NoKeyringError, KeyringError) as exc:
            raise AccountStoreError(NO_KEYRING_MESSAGE) from exc
        return value == "1"

    def get_tencent_session(
        self,
        uid: str,
        provider: TencentLoginProvider = TencentLoginProvider.QQ,
    ) -> TencentSession | None:
        uid = _require_uid(uid)
        provider = _provider(provider)
        try:
            value = keyring.get_password(
                self.config.keyring_service,
                self.config.tencent_keyring_username(uid, provider),
            )
        except (NoKeyringError, KeyringError) as exc:
            raise AccountStoreError(NO_KEYRING_MESSAGE) from exc
        if value is None:
            return None
        try:
            return load_tencent_session(value)
        except ValueError as exc:
            msg = "stored Tencent session is invalid"
            raise AccountStoreError(msg) from exc

    def save_tencent_session(self, session: object, *, authorized: bool) -> None:
        session = _require_tencent_session(session)
        uid = _require_uid(session.uid)
        try:
            keyring.set_password(
                self.config.keyring_service,
                self.config.tencent_keyring_username(uid, session.provider),
                dump_tencent_session(session),
            )
            keyring.set_password(
                self.config.keyring_service,
                self._tencent_authorization_username(uid, session.provider),
                "1" if authorized else "0",
            )
        except (NoKeyringError, KeyringError) as exc:
            raise AccountStoreError(NO_KEYRING_MESSAGE) from exc

    def delete_tencent_session(
        self,
        uid: str,
        provider: TencentLoginProvider = TencentLoginProvider.QQ,
    ) -> None:
        uid = _require_uid(uid)
        provider = _provider(provider)
        try:
            self._delete_password(self.config.tencent_keyring_username(uid, provider))
            self._delete_password(self._tencent_authorization_username(uid, provider))
        except (NoKeyringError, KeyringError) as exc:
            raise AccountStoreError(NO_KEYRING_MESSAGE) from exc

    def is_tencent_authorized(
        self,
        uid: str,
        provider: TencentLoginProvider = TencentLoginProvider.QQ,
    ) -> bool:
        uid = _require_uid(uid)
        provider = _provider(provider)
        try:
            value = keyring.get_password(
                self.config.keyring_service,
                self._tencent_authorization_username(uid, provider),
            )
        except (NoKeyringError, KeyringError) as exc:
            raise AccountStoreError(NO_KEYRING_MESSAGE) from exc
        return value == "1"

    @staticmethod
    def _authorization_username(game_id: GameID, uid: str) -> str:
        return f"authorized:{game_id.value}:{uid}"

    @staticmethod
    def _tencent_authorization_username(
        uid: str,
        provider: TencentLoginProvider = TencentLoginProvider.QQ,
    ) -> str:
        provider = _provider(provider)
        return f"authorized:tencent:{provider.value}:{uid}"

    def _delete_password(self, username: str) -> None:
        try:
            keyring.delete_password(self.config.keyring_service, username)
        except PasswordDeleteError:
            return


def _provider(provider: TencentLoginProvider) -> TencentLoginProvider:
    if isinstance(provider, TencentLoginProvider):
        return provider
    return TencentLoginProvider(str(provider))


def _require_uid(uid: str) -> str:
    normalized = str(uid).strip()
    if not normalized:
        msg = "account uid is required"
        raise AccountStoreError(msg)
    return normalized


def _require_token(token: str) -> str:
    normalized = str(token).strip()
    if not normalized:
        msg = "account token is required"
        raise AccountStoreError(msg)
    return normalized


def _require_tencent_session(session: object) -> TencentSession:
    if not isinstance(session, TencentSession):
        msg = "Tencent session is required"
        raise AccountStoreError(msg)
    return session
