"""Выбор источника данных по конфигурации."""

from __future__ import annotations

from functools import lru_cache

from ..config import settings
from .base import DataSource, DataSourceError
from .demo import DemoDataSource

__all__ = ["DataSource", "DataSourceError", "get_source", "reset_source"]


@lru_cache(maxsize=1)
def get_source() -> DataSource:
    if settings.data_source == "odata":
        from .odata import ODataSource  # импорт здесь: httpx-клиент не нужен в демо-режиме

        return ODataSource()  # type: ignore[return-value]
    return DemoDataSource()  # type: ignore[return-value]


def reset_source() -> None:
    get_source.cache_clear()
