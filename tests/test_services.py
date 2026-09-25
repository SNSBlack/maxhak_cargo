"""Проверки расчётной части. Запуск: python -m pytest tests -q (или python tests/test_services.py)."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.adapters.demo import DEMO_FILE, DemoDataSource  # noqa: E402
from backend.config import ROOT  # noqa: E402
from backend.services import (  # noqa: E402
    catalog,
    cost as cost_service,
    edo as edo_service,
    routing,
)

# Явно маленький набор: тесты формул не должны зависеть от того, какой файл
# выбран в .env (там может стоять сгенерированный масштабный датасет).
SOURCE = DemoDataSource(DEMO_FILE)
LARGE_FILE = ROOT / "data" / "demo_1c_large.json"
SETTINGS = SOURCE.cost_settings()
VEHICLE = SOURCE.vehicles()[0]
DRIVER = SOURCE.drivers()[0]


def test_fuel_norm_winter_surcharge():
    summer, _ = cost_service.fuel_norm_liters(1000, 30.0, 8.0, winter=False)
    winter, _ = cost_service.fuel_norm_liters(1000, 30.0, 8.0, winter=True)
    assert summer == 300.0
    assert round(winter, 1) == 324.0


def test_breakdown_sums_to_total():
    result = cost_service.calculate(
        distance_km=411,
        days=2,
        vehicle=VEHICLE,
        driver=DRIVER,
        cost_settings=SETTINGS,
        federal_share=0.94,
        cargo_value_rub=3_120_000,
        revenue_rub=32_400,
        trip_date="2026-09-22",
    )
    assert abs(sum(i.amount_rub for i in result.items) - result.total_rub) < 0.01
    assert abs(sum(i.share_pct for i in result.items) - 100) < 0.5
    assert result.per_km_rub > 0
    assert all(item.formula for item in result.items)


def test_margin_flags_losing_trip():
    result = cost_service.calculate(
        distance_km=411,
        days=2,
        vehicle=VEHICLE,
        driver=DRIVER,
        cost_settings=SETTINGS,
        revenue_rub=1000,
    )
    assert result.margin_rub < 0
    assert any(w["level"] == "danger" for w in result.warnings)


def test_fuel_overrun_warning():
    result = cost_service.calculate(
        distance_km=400,
        days=1,
        vehicle=VEHICLE,
        driver=DRIVER,
        cost_settings=SETTINGS,
        fuel_fact_l=200.0,
        trip_date="2026-07-01",
    )
    assert any("перерасход" in w["text"].lower() for w in result.warnings)


def test_route_comparison_explains_cheapest():
    result = routing.compare(
        origin="Нижний Новгород",
        destination="Москва",
        vehicle=VEHICLE,
        driver=DRIVER,
        cost_settings=SETTINGS,
        source=SOURCE,
        days=2,
        cargo_value_rub=3_120_000,
        revenue_rub=32_400,
    )
    assert len(result["variants"]) == 3
    cheapest = min(result["variants"], key=lambda v: v["total_rub"])
    assert cheapest["is_cheapest"] is True
    assert result["explanation"]["headline"]


def test_edo_checklist_and_regulation():
    company = SOURCE.company()
    items = edo_service.checklist(company, today=date(2026, 9, 24))
    assert {i["code"] for i in items} >= {"gis_epd", "operator", "ukep", "drivers_ukep", "onec"}
    assert 0 <= edo_service.readiness_score(items) <= 100

    status = edo_service.regulation_status(date(2026, 9, 24))
    assert status["in_force"] is True
    assert status["days"] == 23


def test_edo_documents_flag_rejected():
    trips_by_id = {str(t["id"]): t for t in SOURCE.trips()}
    docs = edo_service.decorate_documents(SOURCE.edo_documents(), trips_by_id)
    rejected = [d for d in docs if d["status"] == "rejected"]
    assert rejected and rejected[0]["needs_action"] is True
    assert rejected[0]["hint"] is not None


def _large_ctx():
    """Контекст на сгенерированном датасете, если он есть в data/."""
    source = DemoDataSource(LARGE_FILE)
    trips = source.trips()
    return {
        "source": source,
        "company": source.company(),
        "cost_settings": source.cost_settings(),
        "vehicles": {str(v["id"]): v for v in source.vehicles()},
        "drivers": {str(d["id"]): d for d in source.drivers()},
        "trips": trips,
        "trips_by_id": {str(t["id"]): t for t in trips},
        "is_mock": True,
    }


def test_search_trips_paginates_and_filters():
    if not LARGE_FILE.exists():
        print("     пропуск: нет data/demo_1c_large.json (сгенерируйте tools/generate_dataset.py)")
        return
    ctx = _large_ctx()

    page = catalog.search_trips(ctx, limit=50)
    assert len(page["trips"]) == 50
    assert page["total"] > 50 and page["has_more"] is True

    second = catalog.search_trips(ctx, limit=50, offset=50)
    assert {t["id"] for t in page["trips"]} & {t["id"] for t in second["trips"]} == set()

    # Активные рейсы всегда наверху первой страницы
    active_total = catalog.search_trips(ctx, status="active", limit=200)["total"]
    assert active_total > 0
    head = [t["status"] for t in page["trips"][:active_total]]
    assert all(s in catalog.ACTIVE_STATUSES for s in head)

    found = catalog.search_trips(ctx, query="Казань", limit=200)
    assert found["total"] > 0
    assert all("Казань" in t["route"] for t in found["trips"])


def test_summary_respects_period():
    if not LARGE_FILE.exists():
        return
    ctx = _large_ctx()
    month = catalog.fleet_summary(ctx, period_days=30, today=date(2026, 9, 24))
    year = catalog.fleet_summary(ctx, period_days=365, today=date(2026, 9, 24))
    assert month["done_trips"] < year["done_trips"]
    assert month["revenue_rub"] < year["revenue_rub"]
    # В ответ уходят только худшие рейсы, а не весь список
    assert len(year["losing_trips"]) <= 5
    assert year["losing_trips_total"] >= len(year["losing_trips"])
    if len(year["losing_trips"]) > 1:
        assert year["losing_trips"][0]["margin_rub"] <= year["losing_trips"][1]["margin_rub"]


def test_generated_dataset_is_realistic():
    if not LARGE_FILE.exists():
        return
    ctx = _large_ctx()
    margins = []
    for trip in ctx["trips"]:
        if trip["status"] != "завершён":
            continue
        margins.append(catalog.cost_for_trip(str(trip["id"]), ctx)["cost"]["margin_pct"])
    assert len(margins) > 500
    losing_share = sum(1 for m in margins if m < 0) / len(margins)
    # Маржа перевозчика тонкая, но не любой рейс убыточен: датасет, где
    # убыточных нет совсем или больше трети, для проверки продукта бесполезен.
    assert 0.02 < losing_share < 0.33, losing_share
    assert 5 < sorted(margins)[len(margins) // 2] < 25


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
