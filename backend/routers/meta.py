"""Служебные данные: кто открыл приложение, на каких источниках оно работает."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..auth import WebAppUser, current_user
from ..config import settings
from ..services import catalog

router = APIRouter(prefix="/api", tags=["meta"])

# Из AGENT.md, раздел 9. Показываем в интерфейсе, чтобы демо-данные не
# принимали за настоящие, а нерешённые вопросы не терялись.
OPEN_QUESTIONS = [
    "Конфигурация 1С пилотного заказчика и тестовый доступ к базе",
    "Оператор ЭДО для пилота",
    "Клиентский трекинг в MVP или в Phase 2",
    "Наличие телематики (ДУТ/CAN) у заказчика",
]


@router.get("/meta", summary="Контекст запуска: пользователь, компания, источники данных")
def meta(user: WebAppUser = Depends(current_user)) -> dict[str, Any]:
    ctx = catalog.context()
    return {
        "user": user.as_dict(),
        "company": ctx["company"],
        "auth_mode": settings.auth_mode,
        "data_source": settings.data_source,
        "edo_provider": settings.edo_provider,
        "routing_engine": settings.routing_engine,
        "data_is_mock": ctx["is_mock"],
        "bot_username": settings.max_bot_username,
        "open_questions": OPEN_QUESTIONS,
    }


@router.get("/health", summary="Проверка живости сервиса")
def health() -> dict[str, str]:
    return {"status": "ok"}
