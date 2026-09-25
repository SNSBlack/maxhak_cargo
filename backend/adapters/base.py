"""Слой источника данных.

Конфигурации 1С у перевозчиков разные (УТ, УНФ, отраслевые ТМС), поэтому вся
работа с данными идёт через единый интерфейс `DataSource`, а маппинг полей
конкретной базы живёт отдельно (см. `odata.py` и `mappings/`).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


class DataSourceError(RuntimeError):
    """Источник данных недоступен или сконфигурирован неполно."""


@runtime_checkable
class DataSource(Protocol):
    """Read-only контракт. Запись в 1С в MVP не предусмотрена."""

    name: str
    is_mock: bool

    def company(self) -> dict[str, Any]: ...

    def cost_settings(self) -> dict[str, Any]: ...

    def vehicles(self) -> list[dict[str, Any]]: ...

    def drivers(self) -> list[dict[str, Any]]: ...

    def trips(self) -> list[dict[str, Any]]: ...

    def edo_documents(self) -> list[dict[str, Any]]: ...

    def fuel_purchases(self) -> list[dict[str, Any]]: ...

    def route_variants(self, origin: str, destination: str) -> list[dict[str, Any]]: ...


def index_by_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row["id"]): row for row in rows}
