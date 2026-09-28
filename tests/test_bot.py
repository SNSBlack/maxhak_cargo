"""Проверки поведения бота. Сеть не нужна: вместо MAX API подставляется фейк,
который ведёт себя как живой API в тех местах, где мы на этом уже обжигались.

Запуск: python tests/test_bot.py (или python -m pytest tests -q).
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import bot, db  # noqa: E402
from backend.config import settings  # noqa: E402
from backend.max_api import MaxApiError  # noqa: E402


class StrictFakeApi:
    """Повторяет проверенное на живом API поведение MAX:
    пустое подтверждение нажатия отклоняется с 400, а сообщение с кнопкой
    open_app на непривязанное мини-приложение отклоняется целиком с 404."""

    def __init__(self, app_registered: bool = False) -> None:
        self.sent: list[tuple[dict, str]] = []
        self.keyboards: list[list] = []
        self.acks: list[str] = []
        self.app_registered = app_registered

    async def send_message(self, text, buttons=None, **target):
        has_app = any(b.get("type") == "open_app" for row in (buttons or []) for b in row)
        if has_app and not self.app_registered:
            raise MaxApiError("MAX API 404: {'code': 'not.found', 'message': 'Link not found'}")
        self.sent.append((target, text))
        self.keyboards.append(buttons or [])
        return {}

    async def answer_callback(self, callback_id, text=None, notification=None):
        if not (text or notification):
            raise MaxApiError("MAX API 400: `message` or `notification` required")
        if text:
            # Ответ с message заменяет исходное сообщение и уносит его кнопки
            raise AssertionError("ответ на нажатие заменил бы сообщение с меню")
        self.acks.append(notification)
        return {}


def _every_callback_payload() -> set[str]:
    keyboards = [
        bot.onboarding_intro()[1],
        bot.onboarding_money()[1],
        bot.onboarding_edo()[1],
        bot.onboarding_trips()[1],
        bot.onboarding_finish()[1],
        bot.main_keyboard(),
    ]
    return {
        b["payload"] for kb in keyboards for row in kb for b in row if b["type"] == "callback"
    }


def _fresh_db() -> Path:
    path = Path(tempfile.mkdtemp()) / "test.sqlite3"
    settings.db_path = str(path)
    db.alerts_journal_migrated = False
    db.init()
    return path


def _callback(payload: str, user_id: int, chat_id: int) -> dict:
    return {
        "update_type": "message_callback",
        "callback": {"payload": payload, "callback_id": "cb", "user": {"user_id": user_id}},
        "message": {"recipient": {"chat_id": chat_id}},
    }


def _message(text: str, user_id: int, chat_id: int) -> dict:
    return {
        "update_type": "message_created",
        "message": {
            "body": {"text": text},
            "sender": {"user_id": user_id, "name": "Тест"},
            "recipient": {"chat_id": chat_id},
        },
    }


def test_onboarding_buttons_answer_even_if_ack_is_strict():
    """Регрессия: кнопки знакомства молчали, потому что пустое подтверждение
    нажатия падало с 400 раньше, чем уходил следующий шаг."""
    _fresh_db()
    api = StrictFakeApi()
    for payload in ["onb:1", "onb:2", "onb:3", "onb:done"]:
        asyncio.run(bot.dispatch(api, _callback(payload, 101, 201)))
    heads = [text.split("\n")[0] for _, text in api.sent]
    assert heads == [
        "Шаг 1 из 3. Деньги.",
        "Шаг 2 из 3. Документы.",
        "Шаг 3 из 3. Рейсы и маршруты.",
        "Готово.",
    ], heads


def test_skip_is_remembered_without_prior_start():
    """Кнопку жмут в старом сообщении, пользователя в базе ещё нет."""
    _fresh_db()
    api = StrictFakeApi()
    asyncio.run(bot.dispatch(api, _callback("onb:skip", 102, 202)))
    assert db.onboarding_step(102) == bot.ONBOARDING_DONE
    asyncio.run(bot.dispatch(api, _message("/start", 102, 202)))
    assert api.sent[-1][1].startswith("С возвращением")


def test_alert_journal_is_per_user():
    """Регрессия: ключ журнала был уникален глобально. Второму пользователю не
    гасилась история, а рассылка падала на UNIQUE constraint у всех."""
    _fresh_db()
    api = StrictFakeApi()
    for user_id, chat_id in [(103, 203), (104, 204)]:
        asyncio.run(bot.dispatch(api, _message("/start", user_id, chat_id)))
    api.sent.clear()

    # Оба подписчика зарегистрированы после накопления backlog: писать некому
    sent = asyncio.run(bot.send_alerts(api))
    assert sent == 0, f"backlog разослан: {sent}"

    # Новое событие получают оба, по одной сводке
    assert db.mark_alert("edo:new-doc", 103) is True
    assert db.mark_alert("edo:new-doc", 104) is True
    assert db.mark_alert("edo:new-doc", 103) is False


def test_every_button_gets_exactly_one_answer():
    """Прожимает каждую callback-кнопку, которую бот вообще умеет показать."""
    _fresh_db()
    payloads = _every_callback_payload()
    assert len(payloads) >= 8, payloads
    for payload in sorted(payloads):
        api = StrictFakeApi()
        asyncio.run(bot.dispatch(api, _callback(payload, 110, 210)))
        assert len(api.sent) == 1, f"{payload}: отправлено {len(api.sent)} сообщений"
        text = api.sent[0][1]
        assert text.strip(), f"{payload}: пустой ответ"
        assert "устарела" not in text, f"{payload}: кнопка не распознана"
        assert len(text) <= 4000, f"{payload}: {len(text)} символов, лимит сообщения MAX 4000"
        assert api.acks and api.acks[0], f"{payload}: нажатие не подтверждено"


def test_menu_survives_button_press():
    """Регрессия: ответ на «Рейсы / ЭДО / Деньги» заменял сообщение с меню."""
    _fresh_db()
    for payload in ["trips", "edo", "money"]:
        api = StrictFakeApi()
        asyncio.run(bot.dispatch(api, _callback(payload, 111, 211)))
        callbacks = {b.get("payload") for row in api.keyboards[0] for b in row}
        assert {"trips", "edo", "money"} <= callbacks, f"{payload}: меню пропало"


def test_app_button_present_and_safe_before_binding():
    """Регрессия: open_app на непривязанный адрес ронял всё сообщение с 404.
    До привязки кнопка приложения должна быть ссылкой, но быть."""
    _fresh_db()
    old_url, old_reg = settings.public_webapp_url, settings.miniapp_registered
    try:
        settings.public_webapp_url = "https://example.trycloudflare.com"
        settings.miniapp_registered = 0
        bot._open_app_rejected = False
        app = [b for row in bot.main_keyboard() for b in row if b["text"] == "Открыть приложение"]
        assert app and app[0]["type"] == "link", app

        # Даже если привязку отметили, а MAX её не видит, ответ всё равно доходит
        settings.miniapp_registered = 1
        assert any(b["type"] == "open_app" for row in bot.main_keyboard() for b in row)
        api = StrictFakeApi(app_registered=False)
        asyncio.run(bot.dispatch(api, _callback("onb:done", 112, 212)))
        assert len(api.sent) == 1 and api.sent[0][1].startswith("Готово")
        sent_types = {b["type"] for row in api.keyboards[0] for b in row}
        assert "open_app" not in sent_types and "link" in sent_types, sent_types
    finally:
        settings.public_webapp_url, settings.miniapp_registered = old_url, old_reg
        bot._open_app_rejected = False


def test_old_alert_journal_is_migrated():
    path = Path(tempfile.mkdtemp()) / "old.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE alerts_sent (key TEXT PRIMARY KEY, max_user_id INTEGER, sent_at TEXT NOT NULL);
        INSERT INTO alerts_sent VALUES ('edo:1', 501, '2026-09-28T00:00:00+00:00');
        """
    )
    conn.commit()
    conn.close()

    settings.db_path = str(path)
    db.alerts_journal_migrated = False
    db.init()

    assert db.alerts_journal_migrated is True
    conn = sqlite3.connect(path)
    pk = [row[1] for row in conn.execute("PRAGMA table_info(alerts_sent)") if row[5]]
    conn.close()
    assert pk == ["key", "max_user_id"], pk
    # Старая запись сохранилась, а тот же ключ для другого пользователя пишется
    assert db.mark_alert("edo:1", 501) is False
    assert db.mark_alert("edo:1", 502) is True


if __name__ == "__main__":
    failures = 0
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            try:
                func()
                print(f"ok   {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    print("\nпровалов:", failures)
    sys.exit(1 if failures else 0)
