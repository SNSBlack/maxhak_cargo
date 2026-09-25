"""Калькулятор себестоимости рейса с разбивкой по статьям.

Главная ценность модуля не в сумме, а в объяснимости: каждая статья несёт
формулу, по которой она посчитана, и источник чисел (1С, норматив, настройка).
Без этого перевозчик не может проверить расчёт и не начинает ему доверять.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

# Статьи, которые обязаны присутствовать в разбивке (AGENT.md, раздел 3).
ITEM_ORDER = [
    "fuel",
    "driver",
    "social",
    "amortization",
    "tires",
    "maintenance",
    "platon",
    "toll",
    "edo",
    "insurance",
    "overhead",
]


@dataclass
class CostItem:
    code: str
    title: str
    amount_rub: float
    formula: str
    source: str
    share_pct: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "title": self.title,
            "amount_rub": round(self.amount_rub, 2),
            "share_pct": round(self.share_pct, 1),
            "formula": self.formula,
            "source": self.source,
        }


@dataclass
class CostResult:
    items: list[CostItem]
    total_rub: float
    per_km_rub: float
    distance_km: float
    fuel_norm_l: float
    revenue_rub: float | None
    margin_rub: float | None
    margin_pct: float | None
    warnings: list[dict[str, str]] = field(default_factory=list)
    inputs: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "items": [i.as_dict() for i in self.items],
            "total_rub": round(self.total_rub, 2),
            "per_km_rub": round(self.per_km_rub, 2),
            "distance_km": round(self.distance_km, 1),
            "fuel_norm_l": round(self.fuel_norm_l, 1),
            "revenue_rub": round(self.revenue_rub, 2) if self.revenue_rub is not None else None,
            "margin_rub": round(self.margin_rub, 2) if self.margin_rub is not None else None,
            "margin_pct": round(self.margin_pct, 1) if self.margin_pct is not None else None,
            "warnings": self.warnings,
            "inputs": self.inputs,
        }


def _num(value: Any, fallback: float = 0.0) -> float:
    try:
        if value is None:
            return fallback
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _money(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ")


def is_winter(when: date | str | None, winter_months: list[int]) -> bool:
    if when is None:
        month = date.today().month
    elif isinstance(when, str):
        try:
            month = datetime.fromisoformat(when[:10]).month
        except ValueError:
            month = date.today().month
    else:
        month = when.month
    return month in winter_months


def fuel_norm_liters(
    distance_km: float,
    norm_l_100km: float,
    winter_surcharge_pct: float,
    winter: bool,
) -> tuple[float, str]:
    """Норма расхода по методике Минтранса: базовая норма плюс зимняя надбавка."""
    base = distance_km * norm_l_100km / 100.0
    if winter and winter_surcharge_pct:
        liters = base * (1 + winter_surcharge_pct / 100.0)
        formula = (
            f"{distance_km:.0f} км x {norm_l_100km} л/100 км / 100 "
            f"x (1 + {winter_surcharge_pct}% зимняя надбавка) = {liters:.1f} л"
        )
    else:
        liters = base
        formula = f"{distance_km:.0f} км x {norm_l_100km} л/100 км / 100 = {liters:.1f} л"
    return liters, formula


def calculate(
    *,
    distance_km: float,
    days: float,
    vehicle: dict[str, Any],
    driver: dict[str, Any],
    cost_settings: dict[str, Any],
    federal_share: float = 0.9,
    toll_road_rub: float = 0.0,
    cargo_value_rub: float = 0.0,
    edo_docs_count: int = 2,
    revenue_rub: float | None = None,
    fuel_fact_l: float | None = None,
    trip_date: str | date | None = None,
    fuel_price_rub_per_l: float | None = None,
) -> CostResult:
    """Считает себестоимость рейса. Все входы явные, чтобы модуль был тестируем."""

    if distance_km <= 0:
        raise ValueError("Пробег должен быть больше нуля")

    days = max(days, 1.0)
    winter_months = cost_settings.get("winter_months") or []
    winter = is_winter(trip_date, winter_months)
    fuel_price = _num(fuel_price_rub_per_l, _num(cost_settings.get("fuel_price_rub_per_l"), 72.0))

    items: list[CostItem] = []
    warnings: list[dict[str, str]] = []

    # 1. Топливо по норме
    norm_l, norm_formula = fuel_norm_liters(
        distance_km,
        _num(vehicle.get("fuel_norm_l_100km"), 32.0),
        _num(vehicle.get("winter_surcharge_pct")),
        winter,
    )
    fuel_rub = norm_l * fuel_price
    items.append(
        CostItem(
            "fuel",
            "Топливо по норме",
            fuel_rub,
            f"{norm_formula}; {norm_l:.1f} л x {fuel_price:.2f} ₽/л = {_money(fuel_rub)} ₽",
            "норма ТС из 1С, цена литра по ГСМ-накладным",
        )
    )

    # 2. Зарплата водителя
    rate = _num(driver.get("rate_rub_per_km"), 9.0)
    daily = _num(driver.get("daily_allowance_rub"), 1400.0)
    wage = rate * distance_km + daily * days
    items.append(
        CostItem(
            "driver",
            "Зарплата водителя",
            wage,
            f"{distance_km:.0f} км x {rate} ₽/км + {days:.0f} сут x {daily:.0f} ₽ "
            f"= {_money(wage)} ₽",
            "ставка водителя из 1С",
        )
    )

    # 3. Страховые взносы на зарплату
    social_pct = _num(cost_settings.get("social_tax_pct"), 30.0)
    social = wage * social_pct / 100.0
    items.append(
        CostItem(
            "social",
            "Страховые взносы",
            social,
            f"{_money(wage)} ₽ x {social_pct:.0f}% = {_money(social)} ₽",
            "настройка компании",
        )
    )

    # 4. Амортизация
    purchase = _num(vehicle.get("purchase_price_rub"))
    resource = _num(vehicle.get("resource_km"))
    if purchase and resource:
        amort_per_km = purchase / resource
        amortization = amort_per_km * distance_km
        amort_formula = (
            f"{_money(purchase)} ₽ / {_money(resource)} км = {amort_per_km:.2f} ₽/км; "
            f"x {distance_km:.0f} км = {_money(amortization)} ₽"
        )
    else:
        amortization = 0.0
        amort_formula = "нет цены или ресурса ТС в 1С, статья не рассчитана"
        warnings.append(
            {
                "level": "info",
                "text": "Амортизация не учтена: в карточке ТС нет цены приобретения или ресурса пробега.",
            }
        )
    items.append(
        CostItem("amortization", "Амортизация ТС", amortization, amort_formula, "карточка ТС из 1С")
    )

    # 5. Шины
    tires_set = _num(vehicle.get("tires_set_rub"))
    tires_res = _num(vehicle.get("tires_resource_km"))
    if tires_set and tires_res:
        tires_per_km = tires_set / tires_res
        tires = tires_per_km * distance_km
        tires_formula = (
            f"{_money(tires_set)} ₽ / {_money(tires_res)} км = {tires_per_km:.2f} ₽/км; "
            f"x {distance_km:.0f} км = {_money(tires)} ₽"
        )
    else:
        tires = 0.0
        tires_formula = "нет данных по комплекту шин, статья не рассчитана"
    items.append(CostItem("tires", "Шины", tires, tires_formula, "карточка ТС из 1С"))

    # 6. Ремонт и ТО
    maint_per_km = _num(vehicle.get("maintenance_rub_per_km"))
    maintenance = maint_per_km * distance_km
    items.append(
        CostItem(
            "maintenance",
            "Ремонт и ТО",
            maintenance,
            f"{distance_km:.0f} км x {maint_per_km} ₽/км = {_money(maintenance)} ₽",
            "норматив по ТС",
        )
    )

    # 7. Платон
    platon_rate = _num(cost_settings.get("platon_rub_per_km"))
    if vehicle.get("platon_applicable") and platon_rate:
        federal_km = distance_km * federal_share
        platon = federal_km * platon_rate
        platon_formula = (
            f"{distance_km:.0f} км x {federal_share * 100:.0f}% федеральных трасс "
            f"= {federal_km:.0f} км; x {platon_rate} ₽/км = {_money(platon)} ₽"
        )
    else:
        platon = 0.0
        platon_formula = "ТС не подпадает под Платон или тариф не задан"
    items.append(CostItem("platon", "Платон", platon, platon_formula, "тариф в настройках"))

    # 8. Платные дороги
    toll = _num(toll_road_rub)
    items.append(
        CostItem(
            "toll",
            "Платные участки",
            toll,
            f"{_money(toll)} ₽ по выбранному маршруту" if toll else "маршрут без платных участков",
            "движок маршрутизации",
        )
    )

    # 9. Комиссия ЭДО
    doc_price = _num(cost_settings.get("edo_doc_price_rub"))
    edo = doc_price * edo_docs_count
    items.append(
        CostItem(
            "edo",
            "Комиссия ЭДО",
            edo,
            f"{edo_docs_count} док. x {doc_price:.2f} ₽ = {_money(edo)} ₽",
            "тариф оператора ЭДО",
        )
    )

    # 10. Страхование груза
    ins_rate = _num(cost_settings.get("cargo_insurance_rate_pct"))
    ins_min = _num(cost_settings.get("cargo_insurance_min_rub"))
    by_rate = cargo_value_rub * ins_rate / 100.0
    insurance = max(by_rate, ins_min) if (ins_rate or ins_min) else 0.0
    if insurance and by_rate < ins_min:
        ins_formula = (
            f"{_money(cargo_value_rub)} ₽ x {ins_rate}% = {_money(by_rate)} ₽, "
            f"взят минимальный платёж {_money(ins_min)} ₽"
        )
    else:
        ins_formula = f"{_money(cargo_value_rub)} ₽ x {ins_rate}% = {_money(insurance)} ₽"
    items.append(
        CostItem("insurance", "Страхование груза", insurance, ins_formula, "тариф страховщика")
    )

    # 11. Накладные расходы
    direct = sum(i.amount_rub for i in items)
    overhead_pct = _num(cost_settings.get("overhead_pct"))
    overhead = direct * overhead_pct / 100.0
    items.append(
        CostItem(
            "overhead",
            "Накладные расходы",
            overhead,
            f"{_money(direct)} ₽ прямых x {overhead_pct}% = {_money(overhead)} ₽",
            "настройка компании",
        )
    )

    total = direct + overhead
    for item in items:
        item.share_pct = (item.amount_rub / total * 100.0) if total else 0.0
    items.sort(key=lambda i: ITEM_ORDER.index(i.code) if i.code in ITEM_ORDER else 99)

    margin_rub = margin_pct = None
    if revenue_rub is not None:
        margin_rub = revenue_rub - total
        margin_pct = (margin_rub / revenue_rub * 100.0) if revenue_rub else None
        if margin_pct is not None and margin_pct < 0:
            warnings.append(
                {
                    "level": "danger",
                    "text": f"Рейс убыточный: себестоимость выше ставки на {_money(abs(margin_rub))} ₽.",
                }
            )
        elif margin_pct is not None and margin_pct < 12:
            warnings.append(
                {
                    "level": "warn",
                    "text": f"Маржа {margin_pct:.1f}%: ниже порога 12%, на котором рейс перестаёт окупать простои.",
                }
            )

    # Перерасход топлива виден только там, где есть факт из путевого листа.
    if fuel_fact_l:
        delta = fuel_fact_l - norm_l
        delta_pct = delta / norm_l * 100.0 if norm_l else 0.0
        if delta_pct > 7:
            warnings.append(
                {
                    "level": "warn",
                    "text": (
                        f"Факт {fuel_fact_l:.1f} л против нормы {norm_l:.1f} л: "
                        f"перерасход {delta:.1f} л ({delta_pct:.0f}%), "
                        f"это {_money(delta * fuel_price)} ₽ сверх расчёта."
                    ),
                }
            )
        elif delta_pct < -7:
            warnings.append(
                {
                    "level": "info",
                    "text": (
                        f"Факт ниже нормы на {abs(delta):.1f} л. "
                        f"Проверьте, все ли заправки проведены в 1С."
                    ),
                }
            )

    return CostResult(
        items=items,
        total_rub=total,
        per_km_rub=total / distance_km,
        distance_km=distance_km,
        fuel_norm_l=norm_l,
        revenue_rub=revenue_rub,
        margin_rub=margin_rub,
        margin_pct=margin_pct,
        warnings=warnings,
        inputs={
            "winter": winter,
            "fuel_price_rub_per_l": round(fuel_price, 2),
            "days": days,
            "federal_share": federal_share,
            "edo_docs_count": edo_docs_count,
            "vehicle": vehicle.get("plate"),
            "driver": driver.get("name"),
        },
    )
