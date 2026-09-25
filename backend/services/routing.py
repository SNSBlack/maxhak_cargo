"""Фасад над внешним движком маршрутизации.

Свой VRP-солвер не пишем (AGENT.md, раздел 8). Задача модуля: взять варианты
маршрута у движка, прогнать каждый через калькулятор себестоимости и объяснить
разницу человеческим языком. Объясняющий слой и есть добавленная ценность.
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx

from ..config import settings
from . import cost as cost_service


class RoutingError(RuntimeError):
    pass


class RoutingEngine(Protocol):
    name: str

    def variants(self, origin: str, destination: str) -> list[dict[str, Any]]: ...


class DemoRoutingEngine:
    """Берёт заранее заданные варианты из демо-датасета."""

    name = "demo"

    def __init__(self, source: Any) -> None:
        self._source = source

    def variants(self, origin: str, destination: str) -> list[dict[str, Any]]:
        return self._source.route_variants(origin, destination)


class YandexRoutingEngine:
    """Интеграция с Яндекс.Маршрутизацией.

    Транспорт написан, но контракт не проверялся: нет ключа API и нет решения
    по тому, используем ли мы 1С:TMS заказчика вместо внешнего движка
    (AGENT.md, раздел 8). Включать только после сверки ответа API с моделью
    варианта маршрута ниже.
    """

    name = "yandex"
    base_url = "https://courier.yandex.ru/vrs/api/v1"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.yandex_routing_api_key
        if not self.api_key:
            raise RoutingError(
                "YANDEX_ROUTING_API_KEY не задан. Пока используйте ROUTING_ENGINE=demo."
            )

    def variants(self, origin: str, destination: str) -> list[dict[str, Any]]:
        try:
            resp = httpx.post(
                f"{self.base_url}/add/mvrp",
                params={"apikey": self.api_key},
                json={"locations": [{"name": origin}, {"name": destination}]},
                timeout=30.0,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise RoutingError(f"Движок маршрутизации недоступен: {exc}") from exc
        # Ответ API нормализуется к модели варианта: name, distance_km,
        # duration_h, federal_share, toll_rub, note.
        raise RoutingError(
            "Ответ Яндекс.Маршрутизации ещё не отображён на внутреннюю модель: "
            "нужен реальный ключ и пример ответа."
        )


def get_engine(source: Any) -> RoutingEngine:
    if settings.routing_engine == "yandex":
        return YandexRoutingEngine()
    return DemoRoutingEngine(source)


def _hours(value: float) -> str:
    h = int(value)
    m = int(round((value - h) * 60))
    return f"{h} ч {m:02d} мин" if m else f"{h} ч"


def _money(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ")


def compare(
    *,
    origin: str,
    destination: str,
    vehicle: dict[str, Any],
    driver: dict[str, Any],
    cost_settings: dict[str, Any],
    source: Any,
    days: float = 1.0,
    cargo_value_rub: float = 0.0,
    revenue_rub: float | None = None,
    edo_docs_count: int = 2,
    trip_date: str | None = None,
) -> dict[str, Any]:
    """Считает себестоимость каждого варианта и объясняет разницу."""

    engine = get_engine(source)
    raw = engine.variants(origin, destination)
    if not raw:
        raise RoutingError(
            f"Движок '{engine.name}' не знает маршрутов для направления "
            f"{origin} - {destination}."
        )

    variants: list[dict[str, Any]] = []
    for item in raw:
        distance = float(item["distance_km"])
        # Длинный маршрут может добавить сутки водителю: считаем по 9 часам за смену.
        duration = float(item.get("duration_h") or distance / 65.0)
        effective_days = max(days, round(duration / 9.0 + 0.4))
        result = cost_service.calculate(
            distance_km=distance,
            days=effective_days,
            vehicle=vehicle,
            driver=driver,
            cost_settings=cost_settings,
            federal_share=float(item.get("federal_share") or 0.9),
            toll_road_rub=float(item.get("toll_rub") or 0.0),
            cargo_value_rub=cargo_value_rub,
            edo_docs_count=edo_docs_count,
            revenue_rub=revenue_rub,
            trip_date=trip_date,
        )
        variants.append(
            {
                "name": item["name"],
                "note": item.get("note", ""),
                "distance_km": distance,
                "duration_h": round(duration, 1),
                "duration_human": _hours(duration),
                "toll_rub": float(item.get("toll_rub") or 0.0),
                "fuel_l": round(result.fuel_norm_l, 1),
                "cost": result.as_dict(),
                "total_rub": round(result.total_rub, 2),
            }
        )

    base = variants[0]
    cheapest = min(variants, key=lambda v: v["total_rub"])
    fastest = min(variants, key=lambda v: v["duration_h"])

    for v in variants:
        v["is_cheapest"] = v["name"] == cheapest["name"]
        v["is_fastest"] = v["name"] == fastest["name"]
        v["delta_vs_base_rub"] = round(v["total_rub"] - base["total_rub"], 2)

    return {
        "origin": origin,
        "destination": destination,
        "engine": engine.name,
        "variants": variants,
        "base": base["name"],
        "cheapest": cheapest["name"],
        "fastest": fastest["name"],
        "explanation": explain(base, cheapest, fastest),
    }


def explain(base: dict[str, Any], cheapest: dict[str, Any], fastest: dict[str, Any]) -> dict[str, Any]:
    """Собирает разбор 'почему этот вариант выгоднее' по статьям затрат."""

    if cheapest["name"] == base["name"]:
        headline = f"Текущий маршрут «{base['name']}» и есть самый дешёвый из доступных."
        drivers: list[str] = []
        if fastest["name"] != base["name"]:
            time_gain = base["duration_h"] - fastest["duration_h"]
            price = fastest["total_rub"] - base["total_rub"]
            drivers.append(
                f"«{fastest['name']}» быстрее на {_hours(time_gain)}, "
                f"но дороже на {_money(price)} ₽."
            )
        return {"headline": headline, "drivers": drivers, "savings_rub": 0.0, "savings_l": 0.0}

    saving = base["total_rub"] - cheapest["total_rub"]
    fuel_diff = base["fuel_l"] - cheapest["fuel_l"]
    time_diff = base["duration_h"] - cheapest["duration_h"]

    headline = (
        f"«{cheapest['name']}» дешевле на {_money(saving)} ₽ "
        f"и на {abs(fuel_diff):.0f} л топлива."
        if fuel_diff > 0
        else f"«{cheapest['name']}» дешевле на {_money(saving)} ₽."
    )

    # Показываем статьи, которые сильнее всего двигают разницу, а не все подряд.
    base_items = {i["code"]: i["amount_rub"] for i in base["cost"]["items"]}
    ranked: list[tuple[float, str]] = []
    for item in cheapest["cost"]["items"]:
        delta = base_items.get(item["code"], 0.0) - item["amount_rub"]
        if abs(delta) < 150:
            continue
        word = "меньше" if delta > 0 else "больше"
        ranked.append((abs(delta), f"{item['title']}: на {_money(abs(delta))} ₽ {word}"))
    ranked.sort(key=lambda pair: -pair[0])
    drivers = [text for _, text in ranked[:4]]

    km_diff = base["distance_km"] - cheapest["distance_km"]
    if abs(km_diff) >= 1:
        drivers.append(
            f"Пробег: {'короче' if km_diff > 0 else 'длиннее'} на {abs(km_diff):.0f} км"
        )
    if abs(time_diff) >= 0.2:
        drivers.append(
            f"Время в пути: {'меньше' if time_diff > 0 else 'больше'} на {_hours(abs(time_diff))}"
        )

    return {
        "headline": headline,
        "drivers": drivers,
        "savings_rub": round(saving, 2),
        "savings_l": round(fuel_diff, 1),
    }
