"""ЭДО-комплаенс: чек-лист готовности и статусы документов."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ..services import catalog, edo as edo_service

router = APIRouter(prefix="/api/edo", tags=["edo"])


@router.get("", summary="Готовность к обязательным ЭПД и документы, требующие действий")
def overview(
    limit: int = Query(default=40, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    only_action: bool = Query(default=True),
) -> dict[str, Any]:
    ctx = catalog.context()
    try:
        provider = edo_service.get_provider(ctx["source"])
        docs = edo_service.decorate_documents(provider.documents(), ctx["trips_by_id"])
    except edo_service.EdoError as exc:
        raise HTTPException(503, str(exc)) from exc

    items = edo_service.checklist(ctx["company"])
    counters = edo_service.summary(docs)
    # Документы уже отсортированы так, что требующие действий идут первыми.
    selected = [d for d in docs if d["needs_action"]] if only_action else docs
    page = selected[offset : offset + limit]

    return {
        "company": ctx["company"],
        "regulation": edo_service.regulation_status(),
        "checklist": items,
        "readiness_pct": edo_service.readiness_score(items),
        "documents": page,
        "documents_total": len(selected),
        "documents_all": len(docs),
        "has_more": offset + limit < len(selected),
        "counters": counters,
        "common_errors": edo_service.COMMON_ERRORS,
        "provider": provider.name,
        "data_is_mock": getattr(provider, "is_mock", False),
    }
