"""Сборка данных рейса из источника и запуск расчёта по нему."""

from __future__ import annotations

from datetime import date, datetime, timedelta  # noqa: F401 - timedelta нужен для периодов
from typing import Any

from ..adapters import get_source
from . import cost as cost_service


class NotFound(LookupError):
    pass


def context() -> dict[str, Any]:
    source = get_source()
    vehicles = {str(v["id"]): v for v in source.vehicles()}
    drivers = {str(d["id"]): d for d in source.drivers()}
    trips = source.trips()
    settings_ = dict(source.cost_settings())
    price, price_note = fuel_price(source)
    if price:
        settings_["fuel_price_rub_per_l"] = price
        settings_["fuel_price_source"] = price_note
    return {
        "source": source,
        "company": source.company(),
        "cost_settings": settings_,
        "vehicles": vehicles,
        "drivers": drivers,
        "trips": trips,
        "trips_by_id": {str(t["id"]): t for t in trips},
        "is_mock": getattr(source, "is_mock", False),
    }


def fuel_price(source: Any, days: int = 30, today: date | None = None) -> tuple[float | None, str]:
    """Средневзвешенная цена литра по ГСМ-накладным: ближе к реальности, чем прайс АЗС."""
    today = today or date.today()
    since = today - timedelta(days=days)
    liters = 0.0
    amount = 0.0
    for row in source.fuel_purchases():
        try:
            when = datetime.fromisoformat(str(row["date"])[:10]).date()
        except (ValueError, KeyError):
            continue
        if when < since:
            continue
        liters += float(row.get("liters") or 0)
        amount += float(row.get("liters") or 0) * float(row.get("price_rub_per_l") or 0)
    if liters <= 0:
        return None, "нет накладных за период, взята цена из настроек"
    return round(amount / liters, 2), f"средневзвешенная по {days} дням ГСМ-накладных"


def enrich(trip: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    vehicle = ctx["vehicles"].get(str(trip.get("vehicle_id")), {})
    driver = ctx["drivers"].get(str(trip.get("driver_id")), {})
    return {
        **trip,
        "vehicle": {"plate": vehicle.get("plate"), "model": vehicle.get("model")},
        "driver": {"name": driver.get("name")},
        "distance_km": trip.get("distance_fact_km") or trip.get("distance_plan_km"),
        "route": f"{trip.get('origin')} - {trip.get('destination')}",
    }


def list_trips(ctx: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    ctx = ctx or context()
    return [enrich(t, ctx) for t in ctx["trips"]]


ACTIVE_STATUSES = {"в пути", "загрузка", "выгрузка"}


def search_trips(
    ctx: dict[str, Any] | None = None,
    *,
    status: str | None = None,
    query: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Постраничная выдача: на годовом объёме отдавать все рейсы разом нельзя."""
    ctx = ctx or context()
    rows = ctx["trips"]

    if status == "active":
        rows = [t for t in rows if t.get("status") in ACTIVE_STATUSES]
    elif status == "done":
        rows = [t for t in rows if t.get("status") == "завершён"]

    if query:
        needle = query.strip().lower()
        vehicles, drivers = ctx["vehicles"], ctx["drivers"]
        def matches(trip: dict[str, Any]) -> bool:
            plate = vehicles.get(str(trip.get("vehicle_id")), {}).get("plate", "")
            driver = drivers.get(str(trip.get("driver_id")), {}).get("name", "")
            haystack = " ".join(
                str(x or "")
                for x in (
                    trip.get("number"), trip.get("origin"), trip.get("destination"),
                    trip.get("customer"), plate, driver,
                )
            ).lower()
            return needle in haystack
        rows = [t for t in rows if matches(t)]

    # Сначала свежие, затем устойчивой сортировкой активные рейсы наверх:
    # это то, на что логист смотрит сегодня.
    rows = sorted(rows, key=lambda t: str(t.get("date_start") or ""), reverse=True)
    rows = sorted(rows, key=lambda t: t.get("status") not in ACTIVE_STATUSES)

    total = len(rows)
    page = rows[offset : offset + limit]
    return {
        "trips": [enrich(t, ctx) for t in page],
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + limit < total,
        "data_is_mock": ctx["is_mock"],
    }


def get_trip(trip_id: str, ctx: dict[str, Any] | None = None) -> dict[str, Any]:
    ctx = ctx or context()
    trip = ctx["trips_by_id"].get(str(trip_id))
    if not trip:
        raise NotFound(f"Рейс {trip_id} не найден")
    return trip


def cost_for_trip(
    trip_id: str,
    ctx: dict[str, Any] | None = None,
    *,
    use_fact_distance: bool = True,
) -> dict[str, Any]:
    ctx = ctx or context()
    trip = get_trip(trip_id, ctx)
    vehicle = ctx["vehicles"].get(str(trip.get("vehicle_id")), {})
    driver = ctx["drivers"].get(str(trip.get("driver_id")), {})
    distance = (
        trip.get("distance_fact_km") if use_fact_distance else None
    ) or trip.get("distance_plan_km")
    result = cost_service.calculate(
        distance_km=float(distance),
        days=float(trip.get("days") or 1),
        vehicle=vehicle,
        driver=driver,
        cost_settings=ctx["cost_settings"],
        federal_share=float(trip.get("federal_share") or 0.9),
        toll_road_rub=float(trip.get("toll_road_rub") or 0),
        cargo_value_rub=float((trip.get("cargo") or {}).get("value_rub") or 0),
        edo_docs_count=len(trip.get("edo_docs") or []) or 2,
        revenue_rub=float(trip["revenue_rub"]) if trip.get("revenue_rub") is not None else None,
        fuel_fact_l=trip.get("fuel_fact_l"),
        trip_date=trip.get("date_start"),
    )
    return {
        "trip": enrich(trip, ctx),
        "cost": result.as_dict(),
        "data_is_mock": ctx["is_mock"],
    }


def fleet_summary(
    ctx: dict[str, Any] | None = None,
    *,
    period_days: int = 30,
    losing_limit: int = 5,
    today: date | None = None,
) -> dict[str, Any]:
    """Сводка для главного экрана: где деньги утекают прямо сейчас.

    Считается за период, а не за всё время: на годовом объёме итог за весь
    период ни о чём не говорит логисту, ему нужен последний месяц.
    """
    ctx = ctx or context()
    since = (today or date.today()) - timedelta(days=period_days)

    def in_period(trip: dict[str, Any]) -> bool:
        raw = str(trip.get("date_start") or "")[:10]
        try:
            return datetime.fromisoformat(raw).date() >= since
        except ValueError:
            return True

    active = [t for t in ctx["trips"] if t.get("status") in ACTIVE_STATUSES]
    done = [t for t in ctx["trips"] if t.get("status") == "завершён" and in_period(t)]

    revenue = margin = 0.0
    losing: list[dict[str, Any]] = []
    overrun_l = 0.0
    for trip in done:
        calc = cost_for_trip(str(trip["id"]), ctx)["cost"]
        revenue += calc["revenue_rub"] or 0
        margin += calc["margin_rub"] or 0
        if (calc["margin_rub"] or 0) < 0:
            losing.append(
                {
                    "id": trip["id"],
                    "number": trip["number"],
                    "route": f"{trip.get('origin')} - {trip.get('destination')}",
                    "margin_rub": calc["margin_rub"],
                }
            )
        if trip.get("fuel_fact_l"):
            overrun_l += max(0.0, float(trip["fuel_fact_l"]) - calc["fuel_norm_l"])

    # В ответ уходят только худшие: на годовом объёме убыточных рейсов сотни,
    # списком целиком он бесполезен и в чате, и в интерфейсе.
    losing.sort(key=lambda x: x["margin_rub"])
    losing_total = len(losing)

    return {
        "period_days": period_days,
        "active_trips": len(active),
        "done_trips": len(done),
        "losing_trips_total": losing_total,
        "revenue_rub": round(revenue, 2),
        "margin_rub": round(margin, 2),
        "margin_pct": round(margin / revenue * 100, 1) if revenue else None,
        "losing_trips": losing[:losing_limit],
        "fuel_overrun_l": round(overrun_l, 1),
        "fuel_overrun_rub": round(
            overrun_l * float(ctx["cost_settings"].get("fuel_price_rub_per_l") or 0), 2
        ),
        "fuel_price_rub_per_l": ctx["cost_settings"].get("fuel_price_rub_per_l"),
        "fuel_price_source": ctx["cost_settings"].get("fuel_price_source"),
        "data_is_mock": ctx["is_mock"],
    }
