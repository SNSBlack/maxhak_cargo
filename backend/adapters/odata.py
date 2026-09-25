"""Read-only адаптер к 1С через HTTP-сервис / REST OData.

Канал и набор сущностей зафиксированы в AGENT.md, раздел 6. Конкретные имена
объектов и реквизитов различаются между конфигурациями, поэтому они не
захардкожены, а берутся из файла маппинга (`mappings/<name>.json`).

Состояние: транспорт и маппинг-слой готовы, но контракт не проверялся на живой
базе. До ответа на открытый вопрос №1 (какая конфигурация у пилотного
заказчика) адаптер включать в прод нельзя: имена сущностей в `mappings/ut_11.json`
взяты как рабочая гипотеза для УТ 11 и подлежат сверке.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from ..config import ROOT, settings
from .base import DataSourceError

MAPPINGS_DIR = Path(__file__).resolve().parent / "mappings"


def load_mapping(name: str) -> dict[str, Any]:
    path = MAPPINGS_DIR / f"{name}.json"
    if not path.exists():
        raise DataSourceError(
            f"Нет маппинга '{name}'. Положите файл в {MAPPINGS_DIR} "
            f"или укажите другой ONEC_MAPPING."
        )
    return json.loads(path.read_text(encoding="utf-8"))


class ODataSource:
    """Тянет справочники и документы из 1С, приводя их к внутренней модели."""

    name = "odata"
    is_mock = False

    def __init__(
        self,
        base_url: str | None = None,
        user: str | None = None,
        password: str | None = None,
        mapping: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = (base_url or settings.onec_base_url).rstrip("/")
        if not self.base_url:
            raise DataSourceError(
                "ONEC_BASE_URL не задан. Пока нет доступа к базе заказчика, "
                "используйте DATA_SOURCE=demo."
            )
        self.mapping = load_mapping(mapping or settings.onec_mapping)
        self._client = httpx.Client(
            base_url=self.base_url,
            auth=(user or settings.onec_user, password or settings.onec_password),
            timeout=timeout,
            headers={"Accept": "application/json"},
        )

    # --- транспорт ---

    def _fetch(self, entity_key: str) -> list[dict[str, Any]]:
        entity = self.mapping["entities"].get(entity_key)
        if not entity:
            raise DataSourceError(f"В маппинге нет сущности '{entity_key}'")
        params = {"$format": "json"}
        if entity.get("filter"):
            params["$filter"] = entity["filter"]
        if entity.get("select"):
            params["$select"] = entity["select"]
        try:
            resp = self._client.get(f"/{entity['path']}", params=params)
            resp.raise_for_status()
        except httpx.HTTPError as exc:  # сеть, авторизация, 5xx на стороне 1С
            raise DataSourceError(f"1С недоступна ({entity_key}): {exc}") from exc
        return resp.json().get("value", [])

    # --- маппинг полей ---

    @staticmethod
    def _pick(row: dict[str, Any], path: str) -> Any:
        """Достаёт значение по пути вида 'Владелец/Наименование'."""
        value: Any = row
        for part in path.split("/"):
            if not isinstance(value, dict):
                return None
            value = value.get(part)
        return value

    def _map_rows(self, rows: list[dict[str, Any]], entity_key: str) -> list[dict[str, Any]]:
        fields: dict[str, str] = self.mapping["entities"][entity_key]["fields"]
        defaults: dict[str, Any] = self.mapping["entities"][entity_key].get("defaults", {})
        mapped = []
        for row in rows:
            item = dict(defaults)
            for internal, external in fields.items():
                item[internal] = self._pick(row, external)
            mapped.append(item)
        return mapped

    # --- контракт DataSource ---

    def company(self) -> dict[str, Any]:
        rows = self._map_rows(self._fetch("company"), "company")
        if not rows:
            raise DataSourceError("1С вернула пустую организацию")
        return rows[0]

    def cost_settings(self) -> dict[str, Any]:
        # Нормативы и ставки (Платон, страховка, накладные) в типовых
        # конфигурациях не хранятся единообразно: берём их из маппинга.
        return self.mapping.get("cost_settings", {})

    def vehicles(self) -> list[dict[str, Any]]:
        return self._map_rows(self._fetch("vehicles"), "vehicles")

    def drivers(self) -> list[dict[str, Any]]:
        return self._map_rows(self._fetch("drivers"), "drivers")

    def trips(self) -> list[dict[str, Any]]:
        return self._map_rows(self._fetch("trips"), "trips")

    def edo_documents(self) -> list[dict[str, Any]]:
        # Статусы ЭДО приходят от оператора, а не из 1С (см. services/edo.py).
        return []

    def fuel_purchases(self) -> list[dict[str, Any]]:
        return self._map_rows(self._fetch("fuel_purchases"), "fuel_purchases")

    def tariffs(self) -> list[dict[str, Any]]:
        return self._map_rows(self._fetch("tariffs"), "tariffs")

    def route_variants(self, origin: str, destination: str) -> list[dict[str, Any]]:
        # Маршруты приходят из движка маршрутизации, не из 1С.
        return []

    def directions(self) -> list[dict[str, str]]:
        seen: set[tuple[str, str]] = set()
        for trip in self.trips():
            pair = (trip.get("origin"), trip.get("destination"))
            if all(pair):
                seen.add(pair)  # type: ignore[arg-type]
        return [{"origin": o, "destination": d} for o, d in sorted(seen)]

    def close(self) -> None:
        self._client.close()
