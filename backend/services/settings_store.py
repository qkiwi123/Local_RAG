"""非敏感运行时设置的 SQLite 单例存储与版本历史。"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from backend.config import settings
from backend.services.metadata_schema import require_structured_database


DEFAULT_HISTORY_LIMIT = 50


class SettingsStoreError(RuntimeError):
    """运行时设置存储错误。"""


class SettingsStoreCorrupted(SettingsStoreError):
    """数据库中的运行时设置不是合法对象。"""


class SettingsRevisionConflict(SettingsStoreError):
    """expected_revision 已过期；API 层将在 S2/S3 映射为 HTTP 409。"""

    def __init__(self, expected_revision: int, actual_revision: int):
        super().__init__(
            f"设置版本冲突: expected_revision={expected_revision}, "
            f"actual_revision={actual_revision}"
        )
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision


@dataclass(frozen=True)
class StoredRuntimeSettings:
    revision: int
    payload: dict
    updated_at: str | None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect(db_path: str) -> sqlite3.Connection:
    require_structured_database(db_path)
    connection = sqlite3.connect(Path(db_path), timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def _contains_sensitive_key(value, path: tuple[str, ...] = ()) -> str | None:
    if not isinstance(value, Mapping):
        if isinstance(value, list):
            for index, item in enumerate(value):
                match = _contains_sensitive_key(item, path + (str(index),))
                if match:
                    return match
        return None
    for key, item in value.items():
        normalized = str(key).strip().lower().replace("-", "_")
        blocked = (
            normalized in {
                "api_key",
                "apikey",
                "password",
                "secret",
                "token",
                "authorization",
                "access_token",
                "refresh_token",
            }
            or normalized.endswith("_api_key")
            or normalized.endswith("_password")
            or normalized.endswith("_secret")
            or normalized.endswith("_token")
        )
        current = path + (str(key),)
        if blocked:
            return ".".join(current)
        match = _contains_sensitive_key(item, current)
        if match:
            return match
    return None


def _encode_payload(payload: Mapping) -> str:
    if not isinstance(payload, Mapping):
        raise TypeError("运行时设置 payload 必须是对象")
    sensitive_path = _contains_sensitive_key(payload)
    if sensitive_path:
        raise ValueError(f"运行时设置禁止包含敏感字段: {sensitive_path}")
    return json.dumps(
        dict(payload),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )


def _decode_payload(raw: str) -> dict:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise SettingsStoreCorrupted("runtime_settings.payload_json 无法解析") from exc
    if not isinstance(value, dict):
        raise SettingsStoreCorrupted("runtime_settings.payload_json 必须是对象")
    sensitive_path = _contains_sensitive_key(value)
    if sensitive_path:
        raise SettingsStoreCorrupted("runtime_settings 中检测到禁止的敏感字段")
    return value


class SettingsStore:
    def __init__(
        self,
        db_path: str | None = None,
        *,
        history_limit: int = DEFAULT_HISTORY_LIMIT,
    ):
        if history_limit <= 0:
            raise ValueError("history_limit 必须大于 0")
        self.db_path = str(db_path or settings.METADATA_DB_PATH)
        self.history_limit = history_limit

    def load(self) -> StoredRuntimeSettings:
        connection = _connect(self.db_path)
        try:
            row = connection.execute(
                """
                SELECT revision, payload_json, updated_at
                FROM runtime_settings WHERE singleton_id = 1
                """
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return StoredRuntimeSettings(revision=0, payload={}, updated_at=None)
        return StoredRuntimeSettings(
            revision=row["revision"],
            payload=_decode_payload(row["payload_json"]),
            updated_at=row["updated_at"],
        )

    def save(
        self,
        payload: Mapping,
        *,
        expected_revision: int,
        changed_keys: list[str] | tuple[str, ...],
    ) -> StoredRuntimeSettings:
        if expected_revision < 0:
            raise ValueError("expected_revision 不能小于 0")
        raw_payload = _encode_payload(payload)
        raw_changed_keys = json.dumps(
            sorted(set(changed_keys)),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        timestamp = _now()
        connection = _connect(self.db_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT revision FROM runtime_settings WHERE singleton_id = 1"
            ).fetchone()
            actual_revision = row["revision"] if row else 0
            if actual_revision != expected_revision:
                raise SettingsRevisionConflict(
                    expected_revision=expected_revision,
                    actual_revision=actual_revision,
                )
            new_revision = actual_revision + 1
            connection.execute(
                """
                INSERT INTO runtime_settings (
                    singleton_id, revision, payload_json, updated_at
                ) VALUES (1, ?, ?, ?)
                ON CONFLICT(singleton_id) DO UPDATE SET
                    revision = excluded.revision,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (new_revision, raw_payload, timestamp),
            )
            connection.execute(
                """
                INSERT INTO runtime_settings_history (
                    revision, payload_json, changed_keys_json, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (new_revision, raw_payload, raw_changed_keys, timestamp),
            )
            connection.execute(
                """
                DELETE FROM runtime_settings_history
                WHERE revision NOT IN (
                    SELECT revision FROM runtime_settings_history
                    ORDER BY revision DESC LIMIT ?
                )
                """,
                (self.history_limit,),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return StoredRuntimeSettings(
            revision=new_revision,
            payload=json.loads(raw_payload),
            updated_at=timestamp,
        )

    def history(self) -> list[dict]:
        """供 S1 验收测试读取；HTTP 历史接口不在本阶段开放。"""
        connection = _connect(self.db_path)
        try:
            rows = connection.execute(
                """
                SELECT revision, payload_json, changed_keys_json, created_at
                FROM runtime_settings_history ORDER BY revision DESC
                """
            ).fetchall()
        finally:
            connection.close()
        return [
            {
                "revision": row["revision"],
                "payload": _decode_payload(row["payload_json"]),
                "changed_keys": json.loads(row["changed_keys_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]
