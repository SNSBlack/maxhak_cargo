"""ЭДО-комплаенс ассистент.

Read-only: читаем статусы документов у оператора ЭДО и считаем готовность
компании. Создание и подписание документов от имени компании в MVP не делаем,
это юридически чувствительная зона (AGENT.md, раздел 7).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import httpx

from ..config import settings

# ФЗ №140-ФЗ: обязательные электронные перевозочные документы.
MANDATORY_SINCE = date(2026, 9, 1)

STATUS_LABELS = {
    "accepted": ("Принят", "ok"),
    "waiting_signature": ("Ждёт подписи", "warn"),
    "draft": ("Черновик", "warn"),
    "rejected": ("Отклонён", "danger"),
    "expired": ("Просрочен", "danger"),
}

# Ошибки, на которых перевозчики спотыкаются чаще всего.
COMMON_ERRORS = [
    {
        "trigger": "инн",
        "title": "ИНН контрагента не совпадает с ГИС ЭПД",
        "fix": "Сверьте ИНН и КПП грузополучателя в карточке контрагента 1С с данными в ГИС ЭПД. "
        "Документ отклоняется автоматически, переподписание не поможет.",
    },
    {
        "trigger": "подпис",
        "title": "Документ висит без подписи",
        "fix": "Документ должен быть подписан до выезда ТС. Проверьте, у кого из ответственных "
        "есть действующая УКЭП и назначены ли права в кабинете оператора.",
    },
    {
        "trigger": "водител",
        "title": "У водителя нет УКЭП",
        "fix": "Водитель подписывает ЭТрН на этапах погрузки и выгрузки. Без УКЭП рейс "
        "оформляется бумажным дубликатом, что снимает смысл перехода на ЭПД.",
    },
]


class EdoError(RuntimeError):
    pass


class DemoEdoProvider:
    """Отдаёт статусы из демо-датасета вместо API оператора."""

    name = "demo"
    is_mock = True

    def __init__(self, source: Any) -> None:
        self._source = source

    def documents(self) -> list[dict[str, Any]]:
        return self._source.edo_documents()


class OperatorEdoProvider:
    """Клиент API аккредитованного оператора ЭДО (Диадок / Астрал / Контур).

    Не подключаемся к ГИС ЭПД напрямую: только через оператора. Конкретный
    оператор для пилота не выбран (открытый вопрос №2 в AGENT.md), поэтому
    здесь общий транспорт, а разбор ответа включается после выбора оператора.
    """

    name = "operator"
    is_mock = False

    def __init__(self) -> None:
        if not (settings.edo_api_base and settings.edo_api_key):
            raise EdoError(
                "EDO_API_BASE / EDO_API_KEY не заданы. Пока используйте EDO_PROVIDER=demo."
            )
        self._client = httpx.Client(
            base_url=settings.edo_api_base.rstrip("/"),
            headers={"Authorization": f"Bearer {settings.edo_api_key}"},
            timeout=30.0,
        )

    def documents(self) -> list[dict[str, Any]]:
        try:
            resp = self._client.get("/documents", params={"limit": 200})
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise EdoError(f"Оператор ЭДО недоступен: {exc}") from exc
        raise EdoError(
            "Разбор ответа оператора не реализован: нужен выбранный оператор "
            "и пример ответа его API."
        )


def get_provider(source: Any):
    if settings.edo_provider == "demo":
        return DemoEdoProvider(source)
    return OperatorEdoProvider()


def _days_since_mandatory(today: date | None = None) -> int:
    return ((today or date.today()) - MANDATORY_SINCE).days


def regulation_status(today: date | None = None) -> dict[str, Any]:
    days = _days_since_mandatory(today)
    if days >= 0:
        return {
            "in_force": True,
            "since": MANDATORY_SINCE.isoformat(),
            "days": days,
            "text": (
                f"Обязательные ЭПД действуют с 1 сентября 2026 года, идёт {days}-й день. "
                f"Бумажный документооборот по перевозкам уже вне правового поля."
            ),
        }
    return {
        "in_force": False,
        "since": MANDATORY_SINCE.isoformat(),
        "days": -days,
        "text": f"До обязательных ЭПД осталось {-days} дней.",
    }


def checklist(company: dict[str, Any], today: date | None = None) -> list[dict[str, Any]]:
    """Чек-лист готовности компании. Каждый пункт: что сделать и почему это важно."""

    today = today or date.today()
    items: list[dict[str, Any]] = []

    items.append(
        {
            "code": "gis_epd",
            "title": "Регистрация в ГИС ЭПД",
            "state": "ok" if company.get("gis_epd_registered") else "danger",
            "detail": "Компания зарегистрирована в системе."
            if company.get("gis_epd_registered")
            else "Без регистрации оператор не сможет передать ЭТрН в ГИС ЭПД.",
        }
    )

    operator = company.get("edo_operator")
    items.append(
        {
            "code": "operator",
            "title": "Договор с оператором ЭДО",
            "state": "ok" if company.get("edo_connected") else "danger",
            "detail": f"Оператор: {operator}." if company.get("edo_connected")
            else "Прямое подключение к ГИС ЭПД невозможно, нужен аккредитованный оператор.",
        }
    )

    valid_until = company.get("ukep_valid_until")
    if valid_until:
        left = (datetime.fromisoformat(valid_until).date() - today).days
        if left < 0:
            state, detail = "danger", f"УКЭП истекла {abs(left)} дней назад, подписание невозможно."
        elif left < 30:
            state, detail = "warn", f"УКЭП действует ещё {left} дней, выпуск новой занимает до 5 рабочих дней."
        else:
            state, detail = "ok", f"УКЭП действует ещё {left} дней."
    else:
        state, detail = "danger", "Срок действия УКЭП не заполнен."
    items.append({"code": "ukep", "title": "УКЭП ответственного", "state": state, "detail": detail})

    total = int(company.get("drivers_total") or 0)
    with_ukep = int(company.get("drivers_with_ukep") or 0)
    if total and with_ukep >= total:
        state, detail = "ok", f"УКЭП есть у всех {total} водителей."
    elif with_ukep:
        state = "warn"
        detail = (
            f"УКЭП есть у {with_ukep} из {total} водителей. Остальные рейсы придётся "
            f"дублировать бумагой."
        )
    else:
        state, detail = "danger", "Ни у одного водителя нет УКЭП."
    items.append({"code": "drivers_ukep", "title": "УКЭП водителей", "state": state, "detail": detail})

    integration = str(company.get("onec_integration") or "нет").lower()
    items.append(
        {
            "code": "onec",
            "title": "Интеграция 1С с оператором",
            "state": "ok" if integration not in {"нет", "no", "none", ""} else "warn",
            "detail": "Документы уходят оператору из 1С."
            if integration not in {"нет", "no", "none", ""}
            else "Документы оформляются в кабинете оператора вручную: это источник расхождений с 1С.",
        }
    )

    items.append(
        {
            "code": "regulation",
            "title": "Соблюдение сроков по 140-ФЗ",
            "state": "ok" if regulation_status(today)["in_force"] and company.get("edo_connected") else "warn",
            "detail": regulation_status(today)["text"],
        }
    )
    return items


def readiness_score(items: list[dict[str, Any]]) -> int:
    weights = {"ok": 1.0, "warn": 0.5, "danger": 0.0}
    if not items:
        return 0
    return round(sum(weights.get(i["state"], 0.0) for i in items) / len(items) * 100)


def decorate_documents(
    docs: list[dict[str, Any]],
    trips_by_id: dict[str, dict[str, Any]],
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    out = []
    for doc in docs:
        label, level = STATUS_LABELS.get(doc.get("status", ""), ("Неизвестно", "warn"))
        trip = trips_by_id.get(str(doc.get("trip_id")), {})
        updated = doc.get("updated_at")
        stale_hours = None
        if updated:
            try:
                stale_hours = (now - datetime.fromisoformat(updated)).total_seconds() / 3600
            except ValueError:
                stale_hours = None
        needs_action = doc.get("status") in {"rejected", "expired"} or (
            doc.get("status") in {"waiting_signature", "draft"}
            and (stale_hours or 0) > 12
        )
        hint = None
        if doc.get("error"):
            hint = next(
                (e for e in COMMON_ERRORS if e["trigger"] in str(doc["error"]).lower()),
                None,
            )
        elif needs_action:
            hint = COMMON_ERRORS[1]
        out.append(
            {
                **doc,
                "status_label": label,
                "status_level": level,
                "trip_number": trip.get("number"),
                "trip_route": f"{trip.get('origin', '')} - {trip.get('destination', '')}".strip(" -"),
                "stale_hours": round(stale_hours, 1) if stale_hours is not None else None,
                "needs_action": needs_action,
                "hint": hint,
            }
        )
    # Порядок: сначала требующие действий, внутри них отклонённые (по ним есть
    # конкретная причина), внутри группы свежие сверху. Устойчивыми сортировками.
    out.sort(key=lambda d: d.get("updated_at") or "", reverse=True)
    out.sort(key=lambda d: (not d["needs_action"], d.get("status") != "rejected"))
    return out


def summary(docs: list[dict[str, Any]]) -> dict[str, int]:
    counts = {key: 0 for key in STATUS_LABELS}
    for doc in docs:
        if doc.get("status") in counts:
            counts[doc["status"]] += 1
    counts["needs_action"] = sum(1 for d in docs if d.get("needs_action"))
    return counts
