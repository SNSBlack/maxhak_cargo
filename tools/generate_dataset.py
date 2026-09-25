"""Генератор тестового датасета грузоперевозок в формате выгрузки из 1С.

Зачем: демо-файл на 6 рейсов годится для проверки формул, но не показывает, как
продукт ведёт себя на реальном объёме (год работы парка, тысячи документов).
Открытых датасетов с нужной семантикой (путевые листы, ЭТрН, Платон, нормы
Минтранса) нет, поэтому данные синтетические, но не случайные:

  - расстояния считаются по координатам реальных городов с коэффициентом
    извилистости дорог, а не берутся от балды;
  - цена литра дрейфует по месяцам, а не одна на весь год;
  - зимой в норму расхода попадает надбавка, поэтому себестоимость сезонная;
  - часть рейсов намеренно убыточна, часть с перерасходом топлива: продукт
    должен их ловить, а на идеальных данных этого не видно;
  - статусы документов ЭДО зависят от возраста рейса: старые в основном
    приняты, свежие висят без подписи.

Запуск:
    python tools/generate_dataset.py --trips 1200 --out data/demo_1c_large.json

Данные помечены как mock и в самом файле, и в интерфейсе приложения.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Координаты реальных городов: из них считается расстояние, чтобы направления
# не противоречили географии (Москва ближе Казани, Новосибирск дальше всех).
CITIES = {
    "Нижний Новгород": (56.33, 44.00),
    "Москва": (55.76, 37.62),
    "Санкт-Петербург": (59.94, 30.31),
    "Казань": (55.80, 49.11),
    "Екатеринбург": (56.84, 60.65),
    "Самара": (53.20, 50.15),
    "Уфа": (54.74, 55.97),
    "Пермь": (58.01, 56.25),
    "Воронеж": (51.67, 39.18),
    "Ростов-на-Дону": (47.23, 39.72),
    "Краснодар": (45.04, 38.98),
    "Волгоград": (48.71, 44.51),
    "Саратов": (51.53, 46.03),
    "Челябинск": (55.16, 61.40),
    "Тюмень": (57.15, 65.53),
    "Новосибирск": (55.03, 82.92),
    "Ярославль": (57.63, 39.87),
    "Чебоксары": (56.13, 47.25),
    "Киров": (58.60, 49.66),
    "Ижевск": (56.85, 53.20),
    "Рязань": (54.63, 39.74),
    "Тула": (54.19, 37.62),
    "Липецк": (52.61, 39.59),
    "Белгород": (50.60, 36.59),
    "Пенза": (53.20, 45.00),
    "Ульяновск": (54.31, 48.40),
    "Оренбург": (51.77, 55.10),
    "Владимир": (56.13, 40.41),
    "Иваново": (57.00, 40.97),
    "Тольятти": (53.51, 49.42),
}

HUB = "Нижний Новгород"

# Направления, где есть платные скоростные участки.
TOLL_CORRIDORS = {
    ("Нижний Новгород", "Москва"),
    ("Нижний Новгород", "Казань"),
    ("Москва", "Санкт-Петербург"),
    ("Москва", "Воронеж"),
    ("Москва", "Ростов-на-Дону"),
    ("Москва", "Краснодар"),
    ("Нижний Новгород", "Санкт-Петербург"),
}

VEHICLE_MODELS = [
    ("Volvo FH 460 4x2 + п/прицеп Schmitz", "тягач", 32.5, 9_850_000, 1_100_000, 412_000, 180_000, 4.8),
    ("Scania R450 + п/прицеп Krone", "тягач", 31.8, 10_240_000, 1_150_000, 398_000, 175_000, 4.55),
    ("Mercedes-Benz Actros 1845 + п/прицеп Kogel", "тягач", 32.1, 10_620_000, 1_120_000, 405_000, 178_000, 4.7),
    ("MAN TGX 18.440 + п/прицеп Schmitz", "тягач", 32.9, 9_480_000, 1_050_000, 389_000, 170_000, 5.1),
    ("КАМАЗ 5490 NEO + п/прицеп НефАЗ", "тягач", 34.2, 7_430_000, 900_000, 286_000, 150_000, 6.15),
    ("МАЗ 5440 + п/прицеп Тонар", "тягач", 35.6, 6_180_000, 850_000, 264_000, 145_000, 6.9),
    ("Sitrak C7H + п/прицеп Тонар", "тягач", 33.4, 6_940_000, 820_000, 251_000, 140_000, 6.4),
    ("ГАЗон Next 10т, изотерм", "среднетоннажник", 19.4, 4_260_000, 600_000, 128_000, 110_000, 3.35),
    ("КАМАЗ 65115 борт 15т", "среднетоннажник", 24.6, 5_310_000, 700_000, 164_000, 120_000, 4.25),
]

PLATE_LETTERS = "АВЕКМНОРСТУХ"
REGIONS = ["152", "52", "16", "50", "77", "21", "58", "163"]

FIRST_NAMES = [
    "Рустам", "Олег", "Артём", "Динар", "Сергей", "Виталий", "Марат", "Павел",
    "Ильдар", "Геннадий", "Антон", "Радик", "Егор", "Вячеслав", "Алмаз",
    "Станислав", "Руслан", "Тимур", "Валерий", "Эдуард", "Никита", "Ренат",
    "Аркадий", "Владислав", "Юрий", "Дамир", "Леонид", "Максим", "Фёдор", "Азат",
]
LAST_NAMES = [
    "Зиганшин", "Пятибратов", "Кулешов", "Гайнуллин", "Мартыненко", "Безруков",
    "Хайруллин", "Ощепков", "Валиуллин", "Тарасевич", "Кожемякин", "Сабиров",
    "Плетнёв", "Бурдуков", "Шаймарданов", "Корнилаев", "Гребенщиков", "Юсупов",
    "Лапердин", "Нуриев", "Синицын", "Мухаметшин", "Ковригин", "Асадуллин",
    "Прошутинский", "Тагиров", "Верещагин", "Шакиров", "Ерофеев", "Галиахметов",
]

CUSTOMERS = [
    ("Агроторг-Поволжье", 71.5, 28_000, "паллетированный груз"),
    ("СтройРезерв НН", 76.0, 34_000, "стройматериалы"),
    ("Молочный Дом", 84.0, 31_000, "изотерм, +2..+6"),
    ("МебельТрансСервис", 68.4, 26_000, "объёмный груз"),
    ("Химпром-Волга", 88.2, 42_000, "опасный груз класс 9"),
    ("Приволжский Металлург", 79.6, 38_000, "металлопрокат"),
    ("Торговый Дом Сормово", 70.8, 27_000, "сборный груз"),
    ("Кама-Агро", 74.3, 29_000, "зерно в биг-бэгах"),
    ("Электросбыт-Урал", 81.0, 33_000, "кабельная продукция"),
    ("Северный Провиант", 86.5, 36_000, "изотерм, заморозка"),
    ("Автокомпонент Плюс", 72.9, 28_500, "автозапчасти"),
    ("Полимер-Трейд", 77.4, 31_500, "полимерное сырьё"),
]

CARGO_BY_TYPE = {
    "паллетированный груз": ("Паллетированный груз, {n} паллет", 14.0, 21.0, 90_000),
    "стройматериалы": ("Сухая строительная смесь, {n} поддонов", 18.0, 23.0, 42_000),
    "изотерм, +2..+6": ("Молочная продукция, изотерм", 12.0, 18.0, 160_000),
    "объёмный груз": ("Мебельные комплектующие", 9.0, 15.0, 95_000),
    "опасный груз класс 9": ("Химическое сырьё в IBC", 15.0, 20.0, 210_000),
    "металлопрокат": ("Металлопрокат, лист", 19.0, 24.0, 88_000),
    "сборный груз": ("Сборный груз, {n} мест", 8.0, 19.0, 120_000),
    "зерно в биг-бэгах": ("Зерно в биг-бэгах", 17.0, 23.0, 34_000),
    "кабельная продукция": ("Кабель силовой, барабаны", 11.0, 18.0, 240_000),
    "изотерм, заморозка": ("Замороженная продукция, -18", 12.0, 19.0, 185_000),
    "автозапчасти": ("Автозапчасти, {n} коробов", 6.0, 14.0, 260_000),
    "полимерное сырьё": ("Гранулят полимерный, мешки", 16.0, 22.0, 105_000),
}

STATIONS = [
    "Лукойл, М-7 421 км", "Газпромнефть, Владимир", "Татнефть, М-7 720 км",
    "Роснефть, Чебоксары", "Шелл, М-12 180 км", "Лукойл, Нижний Новгород",
    "Башнефть, Уфа", "Газпромнефть, М-4 310 км", "Татнефть, Казань",
    "Роснефть, Пермь", "Лукойл, М-11 340 км", "Иркол, Самара",
]

EDO_ERRORS = [
    "Не совпадает ИНН грузополучателя с данными в ГИС ЭПД",
    "Не совпадает КПП грузоотправителя в титуле и в договоре",
    "Отсутствует подпись водителя на этапе погрузки",
    "УКЭП подписанта истекла на дату подписания",
    "Расхождение массы груза с заявленной в заказ-наряде",
    "Не указан код ОКПД2 для позиции груза",
]


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def road_km(origin: str, destination: str) -> float:
    """Дорога длиннее прямой: коэффициент извилистости 1.22 для трасс РФ."""
    return round(haversine_km(CITIES[origin], CITIES[destination]) * 1.22)


def fuel_price_for(month_index: int, rnd: random.Random) -> float:
    """Цена литра дрейфует вверх примерно на 6% за год плюс месячный шум."""
    base = 69.4 * (1 + 0.005 * month_index)
    return round(base + rnd.uniform(-0.7, 0.9), 2)


def make_plate(rnd: random.Random) -> str:
    L = PLATE_LETTERS
    return (
        f"{rnd.choice(L)}{rnd.randint(100, 999)}{rnd.choice(L)}{rnd.choice(L)}"
        f"{rnd.choice(REGIONS)}"
    )


def build_vehicles(count: int, rnd: random.Random) -> list[dict]:
    vehicles = []
    plates: set[str] = set()
    for i in range(count):
        model, kind, norm, price, resource, tires, tires_res, maint = rnd.choice(VEHICLE_MODELS)
        plate = make_plate(rnd)
        while plate in plates:
            plate = make_plate(rnd)
        plates.add(plate)
        wear = rnd.uniform(0.94, 1.12)  # износ парка: нормы и ремонт расходятся по машинам
        vehicles.append(
            {
                "id": f"v{i + 1}",
                "plate": plate,
                "model": model,
                "type": kind,
                "axles": 5 if kind == "тягач" else 2,
                "fuel_norm_l_100km": round(norm * wear, 1),
                "winter_surcharge_pct": 8.0 if kind == "тягач" else 10.0,
                "tank_l": 700 if kind == "тягач" else 210,
                "purchase_price_rub": int(price * rnd.uniform(0.9, 1.05)),
                "resource_km": int(resource),
                "tires_set_rub": int(tires),
                "tires_resource_km": int(tires_res),
                "maintenance_rub_per_km": round(maint * wear, 2),
                "platon_applicable": True,
            }
        )
    return vehicles


def build_drivers(count: int, rnd: random.Random) -> list[dict]:
    names: set[str] = set()
    drivers = []
    for i in range(count):
        name = f"{rnd.choice(FIRST_NAMES)} {rnd.choice(LAST_NAMES)}"
        while name in names:
            name = f"{rnd.choice(FIRST_NAMES)} {rnd.choice(LAST_NAMES)}"
        names.add(name)
        drivers.append(
            {
                "id": f"d{i + 1}",
                "name": name,
                "rate_rub_per_km": round(rnd.uniform(7.6, 9.8), 2),
                "daily_allowance_rub": rnd.choice([1200, 1300, 1450, 1500]),
                # УКЭП есть примерно у двух третей: это и есть типичная картина
                # неготовности парка к обязательным ЭПД
                "ukep": rnd.random() < 0.66,
            }
        )
    return drivers


def build_route_variants(origin: str, destination: str, rnd: random.Random) -> list[dict]:
    base_km = road_km(origin, destination)
    speed = 63 if base_km > 600 else 58
    toll_pair = (origin, destination) in TOLL_CORRIDORS or (destination, origin) in TOLL_CORRIDORS

    variants = [
        {
            "name": "Федеральная трасса, базовый ход",
            "distance_km": base_km,
            "duration_h": round(base_km / speed, 1),
            "federal_share": round(rnd.uniform(0.86, 0.97), 2),
            "toll_rub": 0,
            "note": "бесплатный ход, загруженность на подъездах к городам",
        }
    ]
    if toll_pair:
        toll_km = round(base_km * rnd.uniform(0.98, 1.06))
        variants.append(
            {
                "name": "Платная скоростная",
                "distance_km": toll_km,
                "duration_h": round(toll_km / (speed * 1.28), 1),
                "federal_share": round(rnd.uniform(0.96, 0.99), 2),
                "toll_rub": int(toll_km * rnd.uniform(6.4, 8.2)),
                "note": "меньше времени и разгонов, но платные участки",
            }
        )
    variants.append(
        {
            "name": "Объезд через областные дороги",
            "distance_km": round(base_km * rnd.uniform(1.09, 1.22)),
            "duration_h": round(base_km * rnd.uniform(1.18, 1.34) / speed, 1),
            "federal_share": round(rnd.uniform(0.72, 0.85), 2),
            "toll_rub": 0,
            "note": "используется при перекрытиях и весовом контроле",
        }
    )
    return variants


def generate(
    trips_count: int,
    vehicles_count: int,
    drivers_count: int,
    months: int,
    seed: int,
    today: date,
) -> dict:
    rnd = random.Random(seed)
    vehicles = build_vehicles(vehicles_count, rnd)
    drivers = build_drivers(drivers_count, rnd)
    start_day = today - timedelta(days=months * 30)

    other_cities = [c for c in CITIES if c != HUB]

    trips: list[dict] = []
    edo_documents: list[dict] = []
    fuel_purchases: list[dict] = []
    route_variants: dict[str, list[dict]] = {}
    doc_seq = 0

    for i in range(trips_count):
        offset_days = rnd.randint(0, months * 30)
        start = start_day + timedelta(days=offset_days)

        # Две трети рейсов идут через хаб: так работает реальный парк,
        # завязанный на один склад.
        if rnd.random() < 0.66:
            origin, destination = HUB, rnd.choice(other_cities)
        else:
            origin, destination = rnd.sample(other_cities, 2)
        if rnd.random() < 0.45:
            origin, destination = destination, origin

        key = f"{origin}|{destination}"
        if key not in route_variants:
            route_variants[key] = build_route_variants(origin, destination, rnd)
        base_variant = route_variants[key][0]

        distance_plan = int(base_variant["distance_km"])
        vehicle = rnd.choice(vehicles)
        driver = rnd.choice(drivers)
        customer, rate_km, min_rub, cargo_type = rnd.choice(CUSTOMERS)

        days = max(1, round(distance_plan / 520 + 0.6))
        winter = start.month in (11, 12, 1, 2, 3)
        norm_l = (
            distance_plan
            * vehicle["fuel_norm_l_100km"]
            / 100
            * (1 + (vehicle["winter_surcharge_pct"] / 100 if winter else 0))
        )

        age_days = (today - start).days
        if age_days > days:
            status, progress = "завершён", 100
        elif age_days >= 0:
            status = "в пути" if age_days >= 1 else "загрузка"
            progress = min(95, max(4, int(age_days / max(days, 1) * 100) + rnd.randint(-8, 8)))
        else:
            status, progress = "загрузка", rnd.randint(0, 8)

        distance_fact = (
            int(distance_plan * rnd.uniform(1.0, 1.06)) if status == "завершён" else None
        )

        # Факт расхода: в основном около нормы, но у части рейсов заметный
        # перерасход, а у части не проведены заправки (факт ниже нормы).
        if status == "завершён":
            roll = rnd.random()
            if roll < 0.14:
                factor = rnd.uniform(1.09, 1.24)      # перерасход
            elif roll < 0.22:
                factor = rnd.uniform(0.78, 0.9)       # непроведённые заправки
            else:
                factor = rnd.uniform(0.96, 1.06)
            fuel_fact = round(norm_l * (distance_fact / distance_plan) * factor, 1)
        else:
            fuel_fact = None

        tmpl, w_min, w_max, price_per_t = CARGO_BY_TYPE[cargo_type]
        weight = round(rnd.uniform(w_min, w_max), 1)
        cargo = {
            "name": tmpl.format(n=rnd.randint(8, 33)),
            "weight_t": weight,
            "value_rub": int(weight * price_per_t * rnd.uniform(0.85, 1.2)),
        }

        # Ставка: тариф на километр с торгом. Примерно каждый двенадцатый рейс
        # взят ниже рынка, такие и должны подсвечиваться как убыточные.
        haggle = rnd.uniform(0.82, 0.9) if rnd.random() < 0.085 else rnd.uniform(0.94, 1.12)
        revenue = max(min_rub, int(distance_plan * rate_km * haggle / 100) * 100)

        toll_choice = 0
        if len(route_variants[key]) > 2 and rnd.random() < 0.3:
            toll_variant = route_variants[key][1]
            toll_choice = int(toll_variant["toll_rub"])

        trip_id = f"t{i + 1}"
        trips.append(
            {
                "id": trip_id,
                "number": f"ПЛ-{start.strftime('%Y-%m%d')}-{i + 1:04d}",
                "date_start": start.isoformat(),
                "date_end": (start + timedelta(days=days)).isoformat(),
                "vehicle_id": vehicle["id"],
                "driver_id": driver["id"],
                "origin": origin,
                "destination": destination,
                "route_hint": base_variant["name"],
                "distance_plan_km": distance_plan,
                "distance_fact_km": distance_fact,
                "federal_share": base_variant["federal_share"],
                "toll_road_rub": toll_choice,
                "days": days,
                "fuel_fact_l": fuel_fact,
                "cargo": cargo,
                "customer": customer,
                "revenue_rub": revenue,
                "status": status,
                "progress_pct": progress,
                "eta": (
                    (
                        datetime.combine(start + timedelta(days=days), datetime.min.time())
                        + timedelta(hours=rnd.randint(8, 20))
                    ).isoformat()
                    + "+03:00"
                )
                if status != "завершён"
                else None,
                "edo_docs": [],
            }
        )

        # Документы ЭДО: ЭТрН обязательна, остальные по ситуации.
        doc_types = ["ЭТрН"]
        if rnd.random() < 0.7:
            doc_types.append("ЭЗЗ")
        if rnd.random() < 0.22:
            doc_types.append("ЭПД")

        for doc_type in doc_types:
            doc_seq += 1
            roll = rnd.random()
            if status == "завершён":
                if roll < 0.9:
                    doc_status, error = "accepted", None
                elif roll < 0.96:
                    doc_status, error = "waiting_signature", None
                else:
                    doc_status, error = "rejected", rnd.choice(EDO_ERRORS)
            else:
                if roll < 0.4:
                    doc_status, error = "waiting_signature", None
                elif roll < 0.65:
                    doc_status, error = "draft", None
                elif roll < 0.92:
                    doc_status, error = "accepted", None
                else:
                    doc_status, error = "rejected", rnd.choice(EDO_ERRORS)

            updated = datetime.combine(
                min(start + timedelta(days=rnd.randint(0, days)), today),
                datetime.min.time(),
            ) + timedelta(hours=rnd.randint(6, 21), minutes=rnd.randint(0, 59))
            doc_id = f"e{doc_seq}"
            edo_documents.append(
                {
                    "id": doc_id,
                    "type": doc_type,
                    "number": f"{doc_type}-{start.strftime('%m%d')}-{doc_seq}",
                    "trip_id": trip_id,
                    "operator": "Диадок",
                    "status": doc_status,
                    "updated_at": updated.isoformat() + "+03:00",
                    "error": error,
                }
            )
            trips[-1]["edo_docs"].append(doc_id)

        # Заправки: одна на каждые ~450 км рейса.
        for stop in range(max(1, distance_plan // 450)):
            when = start + timedelta(days=min(stop, days))
            if when > today:
                break
            month_index = (when.year - start_day.year) * 12 + when.month - start_day.month
            fuel_purchases.append(
                {
                    "date": when.isoformat(),
                    "vehicle_id": vehicle["id"],
                    "liters": round(rnd.uniform(160, 460), 1),
                    "price_rub_per_l": fuel_price_for(month_index, rnd),
                    "station": rnd.choice(STATIONS),
                }
            )

    trips.sort(key=lambda t: t["date_start"], reverse=True)
    fuel_purchases.sort(key=lambda f: f["date"], reverse=True)

    with_ukep = sum(1 for d in drivers if d["ukep"])
    return {
        "_meta": {
            "purpose": "ДЕМО-ДАННЫЕ. Синтетическая выгрузка масштаба годовой работы парка.",
            "mock": True,
            "generated_by": "tools/generate_dataset.py",
            "seed": seed,
            "as_of": today.isoformat(),
            "period": f"{start_day.isoformat()} .. {today.isoformat()}",
            "counts": {
                "trips": len(trips),
                "vehicles": len(vehicles),
                "drivers": len(drivers),
                "edo_documents": len(edo_documents),
                "fuel_purchases": len(fuel_purchases),
                "directions": len(route_variants),
            },
            "replace_with": "backend/adapters/odata.py + маппинг полей конкретной конфигурации 1С",
        },
        "company": {
            "name": "ТК Волга-Логистик",
            "inn": "5260417392",
            "kpp": "526001001",
            "fleet_size": len(vehicles),
            "edo_operator": "Диадок",
            "edo_connected": True,
            "gis_epd_registered": True,
            "onec_integration": "нет",
            "ukep_valid_until": (today + timedelta(days=51)).isoformat(),
            "responsible": "Мария Гнеушева, логист",
            "drivers_total": len(drivers),
            "drivers_with_ukep": with_ukep,
        },
        "cost_settings": {
            "fuel_price_rub_per_l": 72.4,
            "fuel_price_source": "средневзвешенная по ГСМ-накладным за 30 дней",
            "platon_rub_per_km": 3.34,
            "platon_rate_as_of": "2026-09-01, значение конфигурируемое: перед пилотом сверить с текущим тарифом системы Платон",
            "social_tax_pct": 30.0,
            "overhead_pct": 8.5,
            "edo_doc_price_rub": 11.5,
            "cargo_insurance_rate_pct": 0.09,
            "cargo_insurance_min_rub": 640.0,
            "winter_months": [11, 12, 1, 2, 3],
        },
        "vehicles": vehicles,
        "drivers": drivers,
        "tariffs": [
            {"customer": c, "rub_per_km": r, "min_rub": m, "cargo_type": t}
            for c, r, m, t in CUSTOMERS
        ],
        "fuel_purchases": fuel_purchases,
        "trips": trips,
        "edo_documents": edo_documents,
        "route_variants": route_variants,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trips", type=int, default=1200)
    parser.add_argument("--vehicles", type=int, default=40)
    parser.add_argument("--drivers", type=int, default=30)
    parser.add_argument("--months", type=int, default=12)
    parser.add_argument("--seed", type=int, default=20260924)
    parser.add_argument("--today", type=str, default=date.today().isoformat())
    parser.add_argument("--out", type=str, default="data/demo_1c_large.json")
    args = parser.parse_args()

    data = generate(
        trips_count=args.trips,
        vehicles_count=args.vehicles,
        drivers_count=args.drivers,
        months=args.months,
        seed=args.seed,
        today=date.fromisoformat(args.today),
    )

    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    counts = data["_meta"]["counts"]
    size_mb = out.stat().st_size / 1024 / 1024
    print(f"Записано: {out} ({size_mb:.1f} МБ)")
    for key, value in counts.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
