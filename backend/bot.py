"""Бот в MAX: точка входа в мини-приложение и канал уведомлений.

Расчёты и карты живут в мини-приложении (чат не тянет калькулятор), бот берёт
на себя вход, короткие сводки и алерты по ЭДО и убыточным рейсам.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from . import db
from .config import settings
from .max_api import MaxApi, MaxApiError, callback_button, link_button, open_app_button
from .services import catalog, edo as edo_service

log = logging.getLogger("cargo.bot")

HELP = (
    "Команды:\n"
    "/trips - рейсы в работе\n"
    "/money - деньги за 30 дней\n"
    "/edo - что горит по документам\n"
    "/app - открыть мини-приложение\n"
    "/tour - показать знакомство заново\n"
    "/notify_off - выключить уведомления"
)

WELCOME = (
    "Cargo Optimizer считает себестоимость рейса по статьям, сравнивает маршруты "
    "по деньгам и следит за документами ЭДО.\n\n" + HELP
)

# --- знакомство ---
#
# Первое сообщение раньше было списком из пяти команд: человек читает стену
# текста и не понимает, с чего начать. Вместо этого три коротких шага, каждый
# показывает живую цифру по компании и ведёт к следующему одной кнопкой.

ONBOARDING_DONE = 99


def onboarding_intro() -> tuple[str, list[list[dict[str, Any]]]]:
    text = (
        "Это Cargo Optimizer. Помогаю увидеть, где рейс теряет деньги.\n\n"
        "Покажу за три шага на ваших данных. Меньше минуты."
    )
    return text, [
        [callback_button("Показать", "onb:1"), callback_button("Пропустить", "onb:skip")]
    ]


def onboarding_money() -> tuple[str, list[list[dict[str, Any]]]]:
    s = catalog.fleet_summary(period_days=30)
    lines = [
        "Шаг 1 из 3. Деньги.",
        "",
        f"За 30 дней: {_plural(s['done_trips'], 'рейс', 'рейса', 'рейсов')}, "
        f"выручка {_money(s['revenue_rub'])}, "
        f"маржа {_money(s['margin_rub'])}"
        + (f" ({s['margin_pct']}%)" if s["margin_pct"] is not None else "") + ".",
    ]
    if s["losing_trips_total"]:
        worst = s["losing_trips"][0]
        lines.append(
            f"Убыточных рейсов: {s['losing_trips_total']}. Худший - {worst['number']}, "
            f"{worst['route']}: {_money(worst['margin_rub'])}."
        )
    lines += [
        "",
        "Себестоимость раскладывается на 11 статей, у каждой видна формула и "
        "откуда взято число. Команда: /money",
    ]
    return "\n".join(lines), [
        [callback_button("Дальше: документы", "onb:2"), callback_button("Хватит", "onb:skip")]
    ]


def onboarding_edo() -> tuple[str, list[list[dict[str, Any]]]]:
    ctx = catalog.context()
    provider = edo_service.get_provider(ctx["source"])
    docs = edo_service.decorate_documents(provider.documents(), ctx["trips_by_id"])
    attention = [d for d in docs if d["needs_action"]]
    score = edo_service.readiness_score(edo_service.checklist(ctx["company"]))

    lines = [
        "Шаг 2 из 3. Документы.",
        "",
        f"Готовность компании к обязательным ЭПД: {score}%.",
    ]
    if attention:
        lines.append(
            "Требуют действий: "
            + _plural(len(attention), "документ", "документа", "документов")
            + ". По отклонённым показываю причину и что чинить."
        )
    else:
        lines.append("Сейчас всё принято, разбирать нечего.")
    lines += ["", "Команда: /edo"]
    return "\n".join(lines), [
        [callback_button("Дальше: рейсы", "onb:3"), callback_button("Хватит", "onb:skip")]
    ]


def onboarding_trips() -> tuple[str, list[list[dict[str, Any]]]]:
    ctx = catalog.context()
    active = [t for t in ctx["trips"] if t.get("status") in catalog.ACTIVE_STATUSES]
    lines = [
        "Шаг 3 из 3. Рейсы и маршруты.",
        "",
        f"Сейчас в работе: {len(active)}." if active else "Активных рейсов сейчас нет.",
        "Для любого направления сравниваю варианты маршрута в рублях и литрах и "
        "объясняю, за счёт каких статей один выгоднее другого.",
        "",
        "Команда: /trips",
    ]
    return "\n".join(lines), [[callback_button("Понятно", "onb:done")]]


def onboarding_finish() -> tuple[str, list[list[dict[str, Any]]]]:
    text = (
        "Готово.\n\n" + HELP + "\n\nУведомления включены: присылаю только новые "
        "события, историю не сыплю. Выключить: /notify_off"
    )
    return text, main_keyboard()


ONBOARDING = {
    "onb:1": (1, onboarding_money),
    "onb:2": (2, onboarding_edo),
    "onb:3": (3, onboarding_trips),
    "onb:done": (ONBOARDING_DONE, onboarding_finish),
}


def _money(value: float | None) -> str:
    if value is None:
        return "нет данных"
    return f"{value:,.0f}".replace(",", " ") + " ₽"


def _plural(count: int, one: str, few: str, many: str) -> str:
    """Согласование числительного: 1 рейс, 2 рейса, 5 рейсов."""
    tail_100, tail_10 = count % 100, count % 10
    if 11 <= tail_100 <= 14:
        word = many
    elif tail_10 == 1:
        word = one
    elif 2 <= tail_10 <= 4:
        word = few
    else:
        word = many
    return f"{count} {word}"


def webapp_configured() -> bool:
    """Есть ли публичный адрес, по которому MAX сможет открыть мини-приложение."""
    return settings.public_webapp_url.strip().startswith("https://")


# Выставляется, если MAX отклонил кнопку open_app (мини-приложение не привязано
# к боту). После этого клавиатуры собираются без неё до перезапуска.
_open_app_rejected = False


def native_app_button_allowed() -> bool:
    return webapp_configured() and bool(settings.miniapp_registered) and not _open_app_rejected


def main_keyboard() -> list[list[dict[str, Any]]]:
    """Клавиатура под сообщением.

    Кнопка приложения есть всегда, когда задан публичный https-адрес, но её тип
    зависит от привязки. Нативная open_app работает только для мини-приложения,
    которое организаторы привязали к боту: с чужим адресом MAX отклоняет всё
    сообщение (404 "Link not found"), и пользователь не получает ответа вовсе.
    До привязки ставим обычную кнопку-ссылку на ту же страницу.
    """
    rows: list[list[dict[str, Any]]] = []
    if webapp_configured():
        url = settings.public_webapp_url.strip()
        if native_app_button_allowed():
            rows.append([open_app_button("Открыть приложение", url)])
        else:
            rows.append([link_button("Открыть приложение", url)])
    rows.append(
        [callback_button("Рейсы сейчас", "trips"), callback_button("Статус ЭДО", "edo")]
    )
    rows.append([callback_button("Деньги за период", "money")])
    return rows


def _degrade_open_app(buttons: list[list[dict[str, Any]]]) -> list[list[dict[str, Any]]]:
    """Нативная кнопка приложения заменяется ссылкой на ту же страницу."""
    return [
        [
            link_button(b["text"], b["web_app"]) if b.get("type") == "open_app" else b
            for b in row
        ]
        for row in buttons
    ]


async def send(
    api: MaxApi,
    text: str,
    buttons: list[list[dict[str, Any]]] | None = None,
    **target: Any,
) -> dict[str, Any]:
    """Отправка сообщения, которая не молчит из-за кнопки приложения.

    Если мини-приложение не привязано к боту, MAX отклоняет сообщение с
    open_app целиком. Тогда переотправляем его с кнопкой-ссылкой и больше
    нативную кнопку не используем, чтобы не терять каждое следующее сообщение.
    """
    global _open_app_rejected
    try:
        return await api.send_message(text, buttons=buttons, **target)
    except MaxApiError as exc:
        has_app = any(b.get("type") == "open_app" for row in (buttons or []) for b in row)
        if not has_app or "not.found" not in str(exc):
            raise
        _open_app_rejected = True
        log.warning(
            "MAX отклонил кнопку open_app: мини-приложение не привязано к боту. "
            "Переотправляю с кнопкой-ссылкой, дальше нативную кнопку не использую."
        )
        return await api.send_message(text, buttons=_degrade_open_app(buttons), **target)


def app_text() -> str:
    if webapp_configured():
        return (
            "Мини-приложение открывается кнопкой ниже: расчёт себестоимости по "
            "статьям, сравнение маршрутов и разбор по документам."
        )
    return (
        "Мини-приложение пока не подключено: у бота не задан публичный адрес.\n\n"
        "Что нужно сделать:\n"
        "1. Поднять backend на публичном https-адресе.\n"
        "2. Прописать адрес в PUBLIC_WEBAPP_URL и перезапустить бота.\n"
        "3. Указать тот же адрес в настройках бота на платформе MAX.\n\n"
        "Пока адреса нет, сводки доступны командами в чате: /trips, /money, /edo."
    )


# --- тексты сводок ---


def trips_text(limit: int = 8) -> str:
    ctx = catalog.context()
    active = [t for t in ctx["trips"] if t.get("status") in catalog.ACTIVE_STATUSES]
    if not active:
        return "Активных рейсов нет. Завершённые смотрите в приложении."
    total = len(active)
    lines = [f"Активные рейсы: {total}." if total > limit else "Активные рейсы:"]
    for trip in active[:limit]:
        enriched = catalog.enrich(trip, ctx)
        lines.append(
            f"\n{trip['number']}, {enriched['route']}\n"
            f"ТС {enriched['vehicle']['plate']}, водитель {enriched['driver']['name']}\n"
            f"Готовность {trip.get('progress_pct', 0)}%, ставка {_money(trip.get('revenue_rub'))}"
        )
    if total > limit:
        lines.append(f"\nПоказаны {limit} из {total}. Остальные в приложении.")
    return "\n".join(lines)


def edo_text() -> str:
    ctx = catalog.context()
    provider = edo_service.get_provider(ctx["source"])
    docs = edo_service.decorate_documents(provider.documents(), ctx["trips_by_id"])
    attention = [d for d in docs if d["needs_action"]]
    status = edo_service.regulation_status()
    score = edo_service.readiness_score(edo_service.checklist(ctx["company"]))

    if not attention:
        return f"Документы ЭДО в порядке. Готовность компании {score}%.\n{status['text']}"

    lines = [
        "Требуют внимания: "
        + _plural(len(attention), "документ", "документа", "документов")
        + f". Готовность компании {score}%."
    ]
    for doc in attention[:5]:
        line = f"\n{doc['type']} {doc['number']}, рейс {doc.get('trip_number')}: {doc['status_label']}"
        if doc.get("error"):
            line += f"\nПричина: {doc['error']}"
        if doc.get("hint"):
            line += f"\nЧто делать: {doc['hint']['fix']}"
        lines.append(line)
    return "\n".join(lines)


def money_text(period_days: int = 30) -> str:
    s = catalog.fleet_summary(period_days=period_days)
    lines = [
        f"За последние {s['period_days']} дней.",
        f"Завершено рейсов: {s['done_trips']}, сейчас в работе: {s['active_trips']}.",
        f"Выручка {_money(s['revenue_rub'])}, маржа {_money(s['margin_rub'])}"
        + (f" ({s['margin_pct']}%)" if s["margin_pct"] is not None else ""),
        f"Цена литра в расчёте: {s['fuel_price_rub_per_l']} ₽, {s['fuel_price_source']}.",
    ]
    if s["fuel_overrun_l"]:
        lines.append(
            f"Перерасход топлива сверх нормы: {s['fuel_overrun_l']} л "
            f"на {_money(s['fuel_overrun_rub'])}."
        )
    if s["losing_trips_total"]:
        lines.append(f"\nУбыточных рейсов: {s['losing_trips_total']}. Худшие:")
        for trip in s["losing_trips"]:
            lines.append(f"  {trip['number']}, {trip['route']}: {_money(trip['margin_rub'])}")
    if s["data_is_mock"]:
        lines.append("\nДанные демонстрационные: база 1С заказчика ещё не подключена.")
    return "\n".join(lines)


# --- обработка апдейтов ---


def register(user_id: int | None, name: str | None, chat_id: int | None) -> None:
    """Регистрирует пользователя и гасит накопленный backlog уведомлений.

    Без этого новый подписчик в первом же цикле рассылки получает всю историю
    проблемных документов: на годовом объёме это сотни сообщений подряд.
    """
    if not user_id:
        return
    is_new = db.upsert_user(user_id, name or "Пользователь", chat_id)
    if is_new:
        primed = db.prime_alerts(user_id, pending_alert_keys())
        log.info("Новый пользователь %s: погашено %d накопленных поводов", user_id, primed)


async def start_or_resume(api: MaxApi, user_id: int | None, target: dict[str, Any]) -> None:
    """Новому показываем знакомство, вернувшемуся - короткое меню без стены текста."""
    step = db.onboarding_step(user_id) if user_id else 0
    if step >= ONBOARDING_DONE:
        await send(api, "С возвращением.\n\n" + HELP, buttons=main_keyboard(), **target)
        return
    intro, buttons = onboarding_intro()
    await send(api, intro, buttons=buttons, **target)


async def handle_message(api: MaxApi, update: dict[str, Any]) -> None:
    message = update.get("message") or {}
    body = message.get("body") or {}
    sender = message.get("sender") or {}
    recipient = message.get("recipient") or {}
    text = (body.get("text") or "").strip().lower()
    user_id = sender.get("user_id")
    chat_id = recipient.get("chat_id")
    target = {"chat_id": chat_id} if chat_id else {"user_id": user_id}

    register(user_id, sender.get("name"), chat_id)

    # Одна команда - один ответ. Клавиатура прикладывается только там, где она
    # к месту, иначе чат превращается в стену кнопок.
    if text.startswith("/start"):
        await start_or_resume(api, user_id, target)
        return

    if text.startswith("/tour"):
        if user_id:
            db.set_onboarding_step(user_id, 0)
        intro, buttons = onboarding_intro()
        await send(api, intro, buttons=buttons, **target)
        return

    if text.startswith("/help"):
        await send(api, HELP, **target)
        return

    if text.startswith("/app"):
        await send(api, 
            app_text(), buttons=main_keyboard() if webapp_configured() else None, **target
        )
        return

    if text.startswith("/trips"):
        await send(api, trips_text(), **target)
        return

    if text.startswith("/edo"):
        await send(api, edo_text(), **target)
        return

    if text.startswith("/money"):
        await send(api, money_text(), **target)
        return

    if text.startswith("/notify_on") or text.startswith("/notify_off"):
        on = text.startswith("/notify_on")
        if user_id:
            db.set_notifications(user_id, edo=on, margin=on)
        await send(api, 
            "Уведомления включены. Присылаю только новые события, историю не сыплю."
            if on
            else "Уведомления выключены.",
            **target,
        )
        return

    await send(api, "Не знаю такой команды.\n\n" + HELP, **target)


async def handle_callback(api: MaxApi, update: dict[str, Any]) -> None:
    callback = update.get("callback") or {}
    payload = str(callback.get("payload") or "")
    callback_id = callback.get("callback_id")
    user = callback.get("user") or {}
    user_id = user.get("user_id")
    chat_id = ((update.get("message") or {}).get("recipient") or {}).get("chat_id")
    target = {"chat_id": chat_id} if chat_id else {"user_id": user_id}

    # Кнопку могут нажать в старом сообщении, когда пользователя ещё нет в базе
    # (например, после её пересоздания). Без регистрации шаг знакомства не
    # сохранится, и после "Пропустить" знакомство покажется снова.
    register(user_id, user.get("name"), chat_id)

    if payload.startswith("onb:"):
        # Шаги знакомства приходят следующим сообщением: так видно прогресс,
        # а предыдущий шаг остаётся в переписке, если нужно вернуться.
        if payload == "onb:skip":
            if user_id:
                db.set_onboarding_step(user_id, ONBOARDING_DONE)
            text, buttons = "Хорошо, не буду занудствовать.\n\n" + HELP, main_keyboard()
            toast = "Знакомство пропущено"
        else:
            step, builder = ONBOARDING.get(payload, (0, onboarding_intro))
            if user_id:
                db.set_onboarding_step(user_id, step)
            text, buttons = builder()
            toast = "Готово" if step == ONBOARDING_DONE else f"Шаг {step} из 3"

        # Сначала следующий шаг, потом подтверждение нажатия. Раньше порядок был
        # обратный, а пустое подтверждение MAX отклоняет с 400 ("message or
        # notification required"): исключение обрывало обработчик, и кнопки
        # выглядели мёртвыми.
        await send(api, text, buttons=buttons, **target)
        if callback_id:
            try:
                await api.answer_callback(callback_id, notification=toast)
            except MaxApiError as exc:
                log.warning("Не удалось подтвердить нажатие кнопки: %s", exc)
        return

    # Ответ новым сообщением с меню. Раньше текст уходил в подтверждение нажатия:
    # в MAX такой ответ заменяет исходное сообщение, и меню с кнопками исчезало
    # после первого же нажатия.
    screens = {
        "trips": (trips_text, "Рейсы в работе"),
        "edo": (edo_text, "Статус документов"),
        "money": (money_text, "Деньги за 30 дней"),
    }
    builder, toast = screens.get(payload, (lambda: "Эта кнопка устарела. " + HELP, "Кнопка устарела"))
    await send(api, builder(), buttons=main_keyboard(), **target)
    if callback_id:
        try:
            await api.answer_callback(callback_id, notification=toast)
        except MaxApiError as exc:
            log.warning("Не удалось подтвердить нажатие кнопки: %s", exc)


async def dispatch(api: MaxApi, update: dict[str, Any]) -> None:
    kind = update.get("update_type")
    try:
        # Только message_created: на message_edited бот отвечал повторно и
        # дублировал сообщения на каждую правку текста.
        if kind == "message_created":
            await handle_message(api, update)
        elif kind == "message_callback":
            await handle_callback(api, update)
        elif kind == "bot_started":
            user = update.get("user") or {}
            user_id = user.get("user_id")
            chat_id = update.get("chat_id")
            register(user_id, user.get("name"), chat_id)
            # MAX присылает bot_started вместе с /start от клиента. Два
            # приветствия подряд выглядят как спам, поэтому на /start в этом
            # же апдейте не отвечаем: ответит handle_message.
            if not str(update.get("payload") or "").startswith("/start"):
                await start_or_resume(
                    api,
                    user_id,
                    {"chat_id": chat_id} if chat_id else {"user_id": user_id},
                )
    except MaxApiError as exc:
        log.warning("Не удалось ответить на апдейт %s: %s", kind, exc)
    except Exception:  # noqa: BLE001 - один битый апдейт не должен ронять поллинг
        log.exception("Ошибка обработки апдейта %s", kind)


# --- фоновые циклы ---


async def poll_updates(api: MaxApi, stop: asyncio.Event) -> None:
    marker: int | None = None
    while not stop.is_set():
        try:
            data = await api.get_updates(marker=marker, timeout=30)
        except MaxApiError as exc:
            log.warning("Long polling прервался: %s", exc)
            await asyncio.sleep(5)
            continue
        for update in data.get("updates", []):
            await dispatch(api, update)
        marker = data.get("marker", marker)


async def watch_alerts(api: MaxApi, stop: asyncio.Event, interval: int = 300) -> None:
    """Раз в интервал смотрит, не появилось ли поводов написать в чат."""
    while not stop.is_set():
        try:
            await send_alerts(api)
        except Exception:  # noqa: BLE001
            log.exception("Сбой при рассылке алертов")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue


def _edo_key(doc: dict[str, Any]) -> str:
    return f"edo:{doc['id']}:{doc['status']}:{doc.get('updated_at')}"


def _margin_key(trip: dict[str, Any]) -> str:
    return f"margin:{trip['number']}:{round(trip['margin_rub'])}"


def pending_alerts(ctx: dict[str, Any] | None = None):
    """Текущие поводы написать: проблемные документы и убыточные рейсы."""
    ctx = ctx or catalog.context()
    provider = edo_service.get_provider(ctx["source"])
    docs = edo_service.decorate_documents(provider.documents(), ctx["trips_by_id"])
    attention = [d for d in docs if d["needs_action"]]
    losing = catalog.fleet_summary(ctx, period_days=7)["losing_trips"]
    return attention, losing


def pending_alert_keys(ctx: dict[str, Any] | None = None) -> list[str]:
    """Ключи всех текущих поводов: ими гасится backlog нового подписчика."""
    try:
        attention, losing = pending_alerts(ctx)
    except Exception:  # noqa: BLE001 - регистрация не должна падать из-за данных
        log.exception("Не удалось собрать текущие поводы для уведомлений")
        return []
    return [_edo_key(d) for d in attention] + [_margin_key(t) for t in losing]


def digest_text(docs: list[dict[str, Any]], trips: list[dict[str, Any]], shown: int = 3) -> str:
    """Одна сводка вместо потока: человек читает сообщение, а не ленту алертов."""
    parts: list[str] = []
    if docs:
        head = f"Новое по документам: {len(docs)}." if len(docs) > 1 else "Новое по документам:"
        block = [head]
        for doc in docs[:shown]:
            item = (
                f"{doc['type']} {doc['number']}, рейс {doc.get('trip_number')}: "
                f"{doc['status_label']}"
            )
            if doc.get("error"):
                item += "\nПричина: " + str(doc["error"])
            elif doc.get("hint"):
                item += "\nЧто делать: " + doc["hint"]["fix"]
            block.append(item)
        if len(docs) > shown:
            block.append(f"Ещё {len(docs) - shown} в приложении.")
        parts.append("\n\n".join(block))

    if trips:
        block = [f"Убыточные рейсы: {len(trips)}."]
        for trip in trips[:shown]:
            block.append(
                f"{trip['number']}, {trip['route']}: {_money(trip['margin_rub'])}"
            )
        parts.append("\n".join(block))

    return "\n\n".join(parts)


async def send_alerts(api: MaxApi, shown: int = 3) -> int:
    """Рассылает только новые поводы и одним сообщением на пользователя за цикл.

    Два правила, без которых уведомления превращаются в спам: новым считается
    только то, что появилось после подписки (история гасится при регистрации),
    и за цикл уходит одна сводка, а не по сообщению на каждое событие.
    """
    ctx = catalog.context()
    try:
        attention, losing = pending_alerts(ctx)
    except Exception:  # noqa: BLE001
        log.exception("Не удалось получить статусы для рассылки")
        return 0

    # Отклонённые вперёд: по ним есть конкретная причина и что чинить.
    attention.sort(key=lambda d: (d.get("status") != "rejected", d.get("updated_at") or ""))

    edo_subs = {row["max_user_id"]: row for row in db.subscribers("edo")}
    margin_subs = {row["max_user_id"]: row for row in db.subscribers("margin")}
    sent = 0

    for user_id in set(edo_subs) | set(margin_subs):
        row = edo_subs.get(user_id) or margin_subs[user_id]
        fresh_docs = [
            d for d in attention if user_id in edo_subs and db.mark_alert(_edo_key(d), user_id)
        ]
        fresh_trips = [
            t for t in losing if user_id in margin_subs and db.mark_alert(_margin_key(t), user_id)
        ]
        if not (fresh_docs or fresh_trips):
            continue
        try:
            await send(api, 
                digest_text(fresh_docs, fresh_trips, shown),
                chat_id=row["chat_id"],
                buttons=main_keyboard(),
            )
            sent += 1
        except MaxApiError as exc:
            log.warning("Сводка не доставлена пользователю %s: %s", user_id, exc)
    return sent


async def run(stop: asyncio.Event | None = None) -> None:
    stop = stop or asyncio.Event()
    api = MaxApi()
    if db.alerts_journal_migrated:
        # Старая схема журнала молча не гасила историю никому, кроме первого
        # пользователя. Гасим сейчас, до первого цикла рассылки.
        keys = pending_alert_keys()
        for user_id in db.all_user_ids():
            db.prime_alerts(user_id, keys)
        log.info("Журнал уведомлений перестроен, история погашена для всех пользователей")
    try:
        me = await api.me()
        log.info("Бот запущен: @%s (%s)", me.get("username"), me.get("name"))
        await asyncio.gather(poll_updates(api, stop), watch_alerts(api, stop))
    finally:
        await api.close()
