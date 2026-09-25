"""Сравнение маршрутов по деньгам с объяснением разницы."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import db
from ..auth import WebAppUser, current_user
from ..services import catalog, routing as routing_service

router = APIRouter(prefix="/api/routes", tags=["routes"])


@router.get("/directions", summary="Направления, для которых движок знает маршруты")
def directions() -> dict[str, Any]:
    ctx = catalog.context()
    source = ctx["source"]
    items = source.directions() if hasattr(source, "directions") else []
    return {"directions": items}


@router.get("/compare", summary="Сравнение вариантов маршрута по деньгам с объяснением разницы")
def compare(
    origin: str = Query(min_length=2, max_length=80),
    destination: str = Query(min_length=2, max_length=80),
    vehicle_id: str = Query(),
    driver_id: str = Query(),
    revenue_rub: float | None = Query(default=None, ge=0),
    cargo_value_rub: float = Query(default=0, ge=0),
    days: float = Query(default=1, ge=1, le=60),
    user: WebAppUser = Depends(current_user),
) -> dict[str, Any]:
    ctx = catalog.context()
    vehicle = ctx["vehicles"].get(vehicle_id)
    driver = ctx["drivers"].get(driver_id)
    if not vehicle or not driver:
        raise HTTPException(404, "ТС или водитель не найдены")

    try:
        result = routing_service.compare(
            origin=origin,
            destination=destination,
            vehicle=vehicle,
            driver=driver,
            cost_settings=ctx["cost_settings"],
            source=ctx["source"],
            days=days,
            cargo_value_rub=cargo_value_rub,
            revenue_rub=revenue_rub,
        )
    except routing_service.RoutingError as exc:
        raise HTTPException(503, str(exc)) from exc

    db.log_calculation(
        user.user_id,
        "route",
        {
            "origin": origin,
            "destination": destination,
            "cheapest": result["cheapest"],
            "savings_rub": result["explanation"]["savings_rub"],
        },
    )
    result["data_is_mock"] = ctx["is_mock"]
    return result
