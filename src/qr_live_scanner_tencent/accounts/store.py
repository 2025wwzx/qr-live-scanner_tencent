from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import keyring
from keyring.errors import KeyringError, NoKeyringError, PasswordDeleteError

from qr_live_scanner_tencent.accounts.session import (
    TencentSession,
    dump_tencent_session,
    load_tencent_session,
)
from qr_live_scanner_tencent.config import AccountConfig
from qr_live_scanner_tencent.interfaces import (
    AccountStoreError,
    GameID,
    TencentAccountIndexEntry,
    TencentLoginProvider,
)

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

    def list_tencent_sessions(
        self,
        provider: TencentLoginProvider = TencentLoginProvider.QQ,
    ) -> list[TencentAccountIndexEntry]:
        provider = _provider(provider)
        entries = [
            TencentAccountIndexEntry(
                uid=uid,
                provider=entry_provider,
                authorized=(entry_provider, uid) in self.authorized_tencent_accounts,
            )
            for entry_provider, uid in self.tencent_sessions
            if entry_provider is provider
        ]
        return sorted(entries, key=lambda entry: entry.uid)


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
            entries, _ = self._load_tencent_index_for_repair(session.provider)
            entries = [entry for entry in entries if entry.uid != uid]
            entries.append(
                TencentAccountIndexEntry(
                    uid=uid,
                    provider=session.provider,
                    authorized=authorized,
                )
            )
            self._save_tencent_index(session.provider, entries)
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
            entries, _ = self._load_tencent_index_for_repair(provider)
            entries = [
                entry for entry in entries if entry.uid != uid
            ]
            self._save_tencent_index(provider, entries)
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

    def list_tencent_sessions(
        self,
        provider: TencentLoginProvider = TencentLoginProvider.QQ,
    ) -> list[TencentAccountIndexEntry]:
        provider = _provider(provider)
        try:
            entries, repaired = self._load_tencent_index_for_repair(provider)
            existing_entries = [
                entry for entry in entries if self._tencent_session_exists(entry.uid, provider)
            ]
            if repaired or len(existing_entries) != len(entries):
                self._save_tencent_index(provider, existing_entries)
            return existing_entries
        except (NoKeyringError, KeyringError) as exc:
            raise AccountStoreError(NO_KEYRING_MESSAGE) from exc

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

    def _load_tencent_index(
        self,
        provider: TencentLoginProvider = TencentLoginProvider.QQ,
    ) -> list[TencentAccountIndexEntry]:
        provider = _provider(provider)
        value = keyring.get_password(
            self.config.keyring_service,
            self.config.tencent_index_username(provider),
        )
        if value is None:
            return []
        try:
            raw_entries = json.loads(value)
            if not isinstance(raw_entries, list):
                raise ValueError
            entries = [_load_tencent_index_entry(item) for item in raw_entries]
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            msg = "stored Tencent account index is invalid"
            raise AccountStoreError(msg) from exc
        return sorted(
            [entry for entry in entries if entry.provider is provider],
            key=lambda entry: entry.uid,
        )

    def _load_tencent_index_for_repair(
        self,
        provider: TencentLoginProvider = TencentLoginProvider.QQ,
    ) -> tuple[list[TencentAccountIndexEntry], bool]:
        try:
            return self._load_tencent_index(provider), False
        except AccountStoreError:
            return [], True

    def _save_tencent_index(
        self,
        provider: TencentLoginProvider,
        entries: list[TencentAccountIndexEntry],
    ) -> None:
        provider = _provider(provider)
        normalized = sorted(
            [entry for entry in entries if entry.provider is provider],
            key=lambda entry: entry.uid,
        )
        payload = [
            {
                "authorized": entry.authorized,
                "provider": entry.provider.value,
                "uid": entry.uid,
            }
            for entry in normalized
        ]
        keyring.set_password(
            self.config.keyring_service,
            self.config.tencent_index_username(provider),
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        )

    def _tencent_session_exists(
        self,
        uid: str,
        provider: TencentLoginProvider = TencentLoginProvider.QQ,
    ) -> bool:
        provider = _provider(provider)
        return (
            keyring.get_password(
                self.config.keyring_service,
                self.config.tencent_keyring_username(uid, provider),
            )
            is not None
        )


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


def _load_tencent_index_entry(raw: Any) -> TencentAccountIndexEntry:
    if not isinstance(raw, dict):
        raise ValueError
    return TencentAccountIndexEntry(
        uid=str(raw.get("uid") or ""),
        provider=TencentLoginProvider(str(raw.get("provider") or TencentLoginProvider.QQ)),
        authorized=bool(raw.get("authorized")),
    )
