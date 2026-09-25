"""SQLite-хранилище бота: то, чего нет в 1С.

1С остаётся источником правды по рейсам и документам. Здесь живут только
подписки на уведомления, история расчётов и журнал отправленных алертов,
чтобы не слать одно и то же дважды.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    max_user_id INTEGER PRIMARY KEY,
    name        TEXT,
    chat_id     INTEGER,
    notify_edo  INTEGER NOT NULL DEFAULT 1,
    notify_margin INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    -- 0 - знакомство не начато, 1..3 - текущий шаг, 99 - пройдено или пропущено
    onboarding  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS calc_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    max_user_id INTEGER,
    kind        TEXT NOT NULL,
    payload     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts_sent (
    key         TEXT PRIMARY KEY,
    max_user_id INTEGER,
    sent_at     TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    settings.db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_file)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        # Миграция для баз, созданных до появления онбординга.
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
        if "onboarding" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN onboarding INTEGER NOT NULL DEFAULT 0")


def onboarding_step(max_user_id: int) -> int:
    with connect() as conn:
        row = conn.execute(
            "SELECT onboarding FROM users WHERE max_user_id = ?", (max_user_id,)
        ).fetchone()
    return int(row["onboarding"]) if row else 0


def set_onboarding_step(max_user_id: int, step: int) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE users SET onboarding = ? WHERE max_user_id = ?", (step, max_user_id)
        )


def upsert_user(max_user_id: int, name: str, chat_id: int | None = None) -> bool:
    """Заводит или обновляет пользователя. True, если пользователь новый."""
    with connect() as conn:
        existing = conn.execute(
            "SELECT 1 FROM users WHERE max_user_id = ?", (max_user_id,)
        ).fetchone()
        conn.execute(
            """
            INSERT INTO users (max_user_id, name, chat_id, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(max_user_id) DO UPDATE SET
                name = excluded.name,
                chat_id = COALESCE(excluded.chat_id, users.chat_id)
            """,
            (max_user_id, name, chat_id, _now()),
        )
        return existing is None


def prime_alerts(max_user_id: int, keys: list[str]) -> int:
    """Помечает уже накопленные поводы как отправленные, ничего не отправляя.

    Без этого новый подписчик при первом же цикле получает весь backlog: на
    годовом объёме это сотни сообщений про документы, которые лежат месяцами.
    Человеку нужны новые события с момента подписки, а не история.
    """
    if not keys:
        return 0
    with connect() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO alerts_sent (key, max_user_id, sent_at) VALUES (?, ?, ?)",
            [(key, max_user_id, _now()) for key in keys],
        )
    return len(keys)


def set_notifications(max_user_id: int, *, edo: bool | None = None, margin: bool | None = None) -> None:
    with connect() as conn:
        if edo is not None:
            conn.execute("UPDATE users SET notify_edo = ? WHERE max_user_id = ?", (int(edo), max_user_id))
        if margin is not None:
            conn.execute(
                "UPDATE users SET notify_margin = ? WHERE max_user_id = ?", (int(margin), max_user_id)
            )


def subscribers(kind: str) -> list[sqlite3.Row]:
    column = {"edo": "notify_edo", "margin": "notify_margin"}[kind]
    with connect() as conn:
        return conn.execute(
            f"SELECT * FROM users WHERE {column} = 1 AND chat_id IS NOT NULL"
        ).fetchall()


def log_calculation(max_user_id: int | None, kind: str, payload: dict[str, Any]) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO calc_log (max_user_id, kind, payload, created_at) VALUES (?, ?, ?, ?)",
            (max_user_id, kind, json.dumps(payload, ensure_ascii=False), _now()),
        )


def recent_calculations(max_user_id: int | None, limit: int = 10) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT kind, payload, created_at FROM calc_log
            WHERE (? IS NULL OR max_user_id = ?)
            ORDER BY id DESC LIMIT ?
            """,
            (max_user_id, max_user_id, limit),
        ).fetchall()
    return [
        {"kind": r["kind"], "created_at": r["created_at"], "payload": json.loads(r["payload"])}
        for r in rows
    ]


def mark_alert(key: str, max_user_id: int) -> bool:
    """Возвращает True, если алерт по этому ключу ещё не отправляли."""
    with connect() as conn:
        exists = conn.execute(
            "SELECT 1 FROM alerts_sent WHERE key = ? AND max_user_id = ?", (key, max_user_id)
        ).fetchone()
        if exists:
            return False
        conn.execute(
            "INSERT INTO alerts_sent (key, max_user_id, sent_at) VALUES (?, ?, ?)",
            (key, max_user_id, _now()),
        )
        return True
