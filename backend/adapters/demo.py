"""Демо-источник: читает `data/demo_1c.json` вместо реальной базы 1С.

Используется, пока нет доступа к базе пилотного заказчика (открытый вопрос №1
в AGENT.md). Структура ответов совпадает с той, что должен отдавать `odata.py`,
поэтому переключение источника не задевает сервисы и фронтенд.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import ROOT, settings
from .base import DataSourceError

DEMO_FILE = ROOT / "data" / "demo_1c.json"


def configured_file() -> Path:
    path = Path(settings.demo_data_file)
    return path if path.is_absolute() else ROOT / path


class DemoDataSource:
    name = "demo"
    is_mock = True

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or configured_file()
        self._cache: dict[str, Any] | None = None
        self._mtime: float | None = None

    def _data(self) -> dict[str, Any]:
        if not self._path.exists():
            raise DataSourceError(f"Нет файла демо-данных: {self._path}")
        mtime = self._path.stat().st_mtime
        if self._cache is None or mtime != self._mtime:
            self._cache = json.loads(self._path.read_text(encoding="utf-8"))
            self._mtime = mtime
        return self._cache

    def company(self) -> dict[str, Any]:
        return self._data()["company"]

    def cost_settings(self) -> dict[str, Any]:
        return self._data()["cost_settings"]

    def vehicles(self) -> list[dict[str, Any]]:
        return self._data()["vehicles"]

    def drivers(self) -> list[dict[str, Any]]:
        return self._data()["drivers"]

    def trips(self) -> list[dict[str, Any]]:
        return self._data()["trips"]

    def edo_documents(self) -> list[dict[str, Any]]:
        return self._data()["edo_documents"]

    def fuel_purchases(self) -> list[dict[str, Any]]:
        return self._data()["fuel_purchases"]

    def tariffs(self) -> list[dict[str, Any]]:
        return self._data().get("tariffs", [])

    def route_variants(self, origin: str, destination: str) -> list[dict[str, Any]]:
        key = f"{origin}|{destination}"
        variants = self._data().get("route_variants", {}).get(key, [])
        return [dict(v) for v in variants]

    def directions(self) -> list[dict[str, str]]:
        out = []
        for key in self._data().get("route_variants", {}):
            origin, destination = key.split("|", 1)
            out.append({"origin": origin, "destination": destination})
        return out
