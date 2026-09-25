"""Клиентский трекинг: где груз и сколько это стоит."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ..services import catalog, tracking as tracking_service

router = APIRouter(prefix="/api/tracking", tags=["tracking"])


@router.get("", summary="Грузы в пути")
def active() -> dict[str, Any]:
    return {"shipments": tracking_service.active_shipments()}


@router.get("/{trip_id}", summary="Статус груза, срок прибытия и ставка для клиента")
def shipment(trip_id: str) -> dict[str, Any]:
    try:
        return tracking_service.shipment(trip_id)
    except catalog.NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
