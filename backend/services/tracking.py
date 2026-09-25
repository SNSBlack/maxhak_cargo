"""Клиентский трекинг: статус груза, ETA и прозрачная цена.

Боль №4 из AGENT.md: грузоотправитель не видит, где груз и из чего сложилась
цена. Отдаём ему срез по рейсу без внутренней кухни перевозчика (себестоимость
и маржа сюда не попадают).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import catalog, edo as edo_service

STAGE_ORDER = ["принят", "загрузка", "в пути", "выгрузка", "завершён"]


def _eta_text(eta: str | None, now: datetime | None = None) -> str | None:
    if not eta:
        return None
    try:
        when = datetime.fromisoformat(eta)
    except ValueError:
        return None
    now = now or datetime.now(when.tzinfo or timezone.utc)
    delta = when - now
    hours = delta.total_seconds() / 3600
    if hours < 0:
        return f"Плановое время прибытия прошло {abs(hours):.0f} ч назад"
    if hours < 1:
        return f"Прибытие через {max(1, int(hours * 60))} мин, {when.strftime('%H:%M')}"
    if hours < 24:
        return f"Прибытие через {hours:.0f} ч, {when.strftime('%H:%M')}"
    return f"Прибытие {when.strftime('%d.%m')} в {when.strftime('%H:%M')}"


def docs_by_trip(ctx: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Индекс документов по рейсам, посчитанный один раз на контекст.

    Без него каждый груз в списке прогонял бы весь массив документов заново:
    на годовом объёме это тысячи лишних проходов на один запрос.
    """
    cached = ctx.get("_docs_by_trip")
    if cached is not None:
        return cached
    index: dict[str, list[dict[str, Any]]] = {}
    for doc in edo_service.decorate_documents(
        edo_service.get_provider(ctx["source"]).documents(), ctx["trips_by_id"]
    ):
        index.setdefault(str(doc.get("trip_id")), []).append(doc)
    ctx["_docs_by_trip"] = index
    return index


def shipment(trip_id: str, ctx: dict[str, Any] | None = None) -> dict[str, Any]:
    ctx = ctx or catalog.context()
    trip = catalog.get_trip(trip_id, ctx)
    docs = docs_by_trip(ctx).get(str(trip_id), [])

    status = str(trip.get("status") or "").lower()
    current = status if status in STAGE_ORDER else "в пути"
    stages = [
        {
            "name": stage,
            "done": STAGE_ORDER.index(stage) < STAGE_ORDER.index(current),
            "current": stage == current,
        }
        for stage in STAGE_ORDER
    ]

    distance = trip.get("distance_fact_km") or trip.get("distance_plan_km") or 0
    progress = float(trip.get("progress_pct") or 0)
    return {
        "trip_id": trip["id"],
        "number": trip["number"],
        "customer": trip.get("customer"),
        "route": f"{trip.get('origin')} - {trip.get('destination')}",
        "cargo": trip.get("cargo"),
        "status": trip.get("status"),
        "stages": stages,
        "progress_pct": progress,
        "distance_km": distance,
        "distance_left_km": round(distance * (1 - progress / 100)),
        "eta": trip.get("eta"),
        "eta_text": _eta_text(trip.get("eta")),
        "price_rub": trip.get("revenue_rub"),
        "price_note": "Ставка по договору, без дополнительных сборов.",
        "documents": [
            {
                "type": d.get("type"),
                "number": d.get("number"),
                "status_label": d.get("status_label"),
                "status_level": d.get("status_level"),
            }
            for d in docs
        ],
        "vehicle": ctx["vehicles"].get(str(trip.get("vehicle_id")), {}).get("plate"),
        "data_is_mock": ctx["is_mock"],
    }


def active_shipments(ctx: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    ctx = ctx or catalog.context()
    return [
        shipment(str(t["id"]), ctx)
        for t in ctx["trips"]
        if t.get("status") in {"в пути", "загрузка", "выгрузка"}
    ]
