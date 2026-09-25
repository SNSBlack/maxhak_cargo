"""Запуск: API мини-приложения и бот в одном процессе.

    python run.py

Бот стартует внутри lifespan приложения, если задан MAX_BOT_TOKEN и ENABLE_BOT=1.
"""

from __future__ import annotations

import uvicorn

from backend.config import settings

if __name__ == "__main__":
    uvicorn.run(
        "backend.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        log_level="info",
    )
