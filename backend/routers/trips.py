"""Рейсы, справочники и калькулятор себестоимости."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .. import db
from ..auth import WebAppUser, current_user
from ..services import catalog, cost as cost_service

router = APIRouter(prefix="/api", tags=["trips"])


class CostRequest(BaseModel):
    distance_km: float = Field(gt=0, le=20000)
    days: float = Field(default=1, ge=1, le=60)
    vehicle_id: str
    driver_id: str
    federal_share: float = Field(default=0.9, ge=0, le=1)
    toll_road_rub: float = Field(default=0, ge=0)
    cargo_value_rub: float = Field(default=0, ge=0)
    edo_docs_count: int = Field(default=2, ge=0, le=20)
    revenue_rub: float | None = Field(default=None, ge=0)
    trip_date: str | None = None


@router.get("/trips", summary="Список рейсов с фильтрами, поиском и постраничностью")
def list_trips(
    status: str | None = Query(default=None, pattern="^(active|done)$"),
    q: str | None = Query(default=None, max_length=80),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    return catalog.search_trips(status=status, query=q, limit=limit, offset=offset)


@router.get("/trips/{trip_id}", summary="Рейс и его себестоимость по статьям")
def trip_detail(trip_id: str) -> dict[str, Any]:
    try:
        return catalog.cost_for_trip(trip_id)
    except catalog.NotFound as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/summary", summary="Сводка по парку за период: выручка, маржа, перерасход топлива")
def summary(period_days: int = Query(default=30, ge=1, le=730)) -> dict[str, Any]:
    return catalog.fleet_summary(period_days=period_days)


@router.get("/catalog", summary="Справочники: транспорт, водители, настройки расчёта")
def catalog_data() -> dict[str, Any]:
    ctx = catalog.context()
    return {
        "vehicles": list(ctx["vehicles"].values()),
        "drivers": list(ctx["drivers"].values()),
        "cost_settings": ctx["cost_settings"],
        "company": ctx["company"],
        "data_is_mock": ctx["is_mock"],
    }


@router.post("/cost/calc", summary="Расчёт себестоимости произвольного рейса")
def calc(req: CostRequest, user: WebAppUser = Depends(current_user)) -> dict[str, Any]:
    ctx = catalog.context()
    vehicle = ctx["vehicles"].get(req.vehicle_id)
    driver = ctx["drivers"].get(req.driver_id)
    if not vehicle or not driver:
        raise HTTPException(404, "ТС или водитель не найдены")

    result = cost_service.calculate(
        distance_km=req.distance_km,
        days=req.days,
        vehicle=vehicle,
        driver=driver,
        cost_settings=ctx["cost_settings"],
        federal_share=req.federal_share,
        toll_road_rub=req.toll_road_rub,
        cargo_value_rub=req.cargo_value_rub,
        edo_docs_count=req.edo_docs_count,
        revenue_rub=req.revenue_rub,
        trip_date=req.trip_date,
    ).as_dict()

    db.log_calculation(
        user.user_id,
        "cost",
        {"request": req.model_dump(), "total_rub": result["total_rub"]},
    )
    return {"cost": result, "data_is_mock": ctx["is_mock"]}


@router.get("/history", summary="История расчётов пользователя")
def history(user: WebAppUser = Depends(current_user)) -> dict[str, Any]:
    return {"items": db.recent_calculations(user.user_id)}
