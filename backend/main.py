"""FastAPI-приложение: API мини-аппа, раздача фронтенда и запуск бота."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .adapters import DataSourceError
from .assets import render_index
from .config import ROOT, settings
from .routers import edo, meta, routes, tracking, trips

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("cargo.app")


def local_addresses() -> list[str]:
    """Адреса, по которым приложение открывается с этой машины и из локальной сети.

    Публичного адреса у нас нет, а мессенджер открывает мини-приложение
    браузером на телефоне пользователя, поэтому 127.0.0.1 ему недоступен.
    Адрес в локальной сети годится, чтобы посмотреть приложение с телефона
    в обычном браузере, пока адрес для MAX не выдан.
    """
    import socket

    urls = [f"http://127.0.0.1:{settings.port}"]
    if settings.host not in {"0.0.0.0", "::"}:
        return urls

    # Определяем адреса перебором интерфейсов, а не маршрутом наружу: при
    # включённом VPN маршрут уводит на адрес туннеля (198.18.x.x), а нужен
    # адрес в домашней сети, по которому достучится телефон.
    candidates: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip.startswith(("192.168.", "10.")) or (
                ip.startswith("172.") and 16 <= int(ip.split(".")[1]) <= 31
            ):
                if ip not in candidates:
                    candidates.append(ip)
    except socket.gaierror:
        pass
    urls += [f"http://{ip}:{settings.port}" for ip in candidates]
    return urls


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    for url in local_addresses():
        log.info("Мини-приложение доступно: %s", url)
    if not settings.public_webapp_url.strip().startswith("https://"):
        log.info(
            "PUBLIC_WEBAPP_URL не задан: кнопка запуска мини-приложения в чате скрыта, "
            "бот работает командами /trips, /money, /edo"
        )
    stop = asyncio.Event()
    task: asyncio.Task | None = None
    if settings.enable_bot and settings.max_bot_token:
        from .bot import run as run_bot

        task = asyncio.create_task(run_bot(stop))
        log.info("Бот запускается в фоне")
    else:
        log.info("Бот выключен (ENABLE_BOT=0 или нет токена)")
    try:
        yield
    finally:
        stop.set()
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass


API_DESCRIPTION = """
Собственное API решения Cargo Optimizer: мини-приложение MAX обращается только
к этим методам.

**Источники данных.** Все внешние интеграции сейчас работают в демо-режиме и
помечены в ответах полем `data_is_mock`. Смоделированы: выгрузка из 1С
(адаптер OData написан, но не сверен с живой базой), API оператора ЭДО и движок
маршрутизации. Что нужно для перехода на боевые источники, описано в README.

**Авторизация.** Мини-приложение передаёт подпись запуска в заголовке
`X-Max-Init-Data`. Поведение задаётся переменной `AUTH_MODE`: `strict` отклоняет
запросы без валидной подписи, `lenient` помечает сессию неподтверждённой,
`dev` проверку не делает.
"""

TAGS_METADATA = [
    {"name": "meta", "description": "Служебные методы: кто открыл приложение, на каких источниках оно работает."},
    {"name": "trips", "description": "Рейсы, справочники и калькулятор себестоимости с разбивкой по статьям."},
    {"name": "edo", "description": "ЭДО-комплаенс: чек-лист готовности компании и статусы документов."},
    {"name": "routes", "description": "Сравнение вариантов маршрута по деньгам с объяснением разницы."},
    {"name": "tracking", "description": "Клиентский трекинг груза: стадия, срок прибытия, ставка."},
]

app = FastAPI(
    title="Cargo Optimizer API",
    description=API_DESCRIPTION,
    version="1.0.0",
    openapi_tags=TAGS_METADATA,
    contact={"name": "Команда Cargo Optimizer", "url": "https://max.ru/t459_hakaton_max_bot"},
    license_info={"name": "Проприетарное решение хакатона MAX"},
    servers=[
        {"url": settings.public_webapp_url or "http://127.0.0.1:8099", "description": "Текущий контур"},
    ],
    lifespan=lifespan,
)

# Мини-приложение открывается внутри MAX, домен клиента отличается от нашего.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://max.ru", "https://web.max.ru"]
    + ([settings.public_webapp_url] if settings.public_webapp_url else [])
    + (["*"] if settings.auth_mode == "dev" else []),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.exception_handler(DataSourceError)
async def data_source_error(request: Request, exc: DataSourceError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


app.include_router(meta.router)
app.include_router(trips.router)
app.include_router(edo.router)
app.include_router(routes.router)
app.include_router(tracking.router)

@app.middleware("http")
async def cache_headers(request: Request, call_next):
    """Явные правила кэша вместо эвристики браузера.

    Без Cache-Control браузер внутри MAX держал старые css и js и показывал
    прежнюю версию сайта после обновления.
    """
    response = await call_next(request)
    path = request.url.path
    if path.endswith((".css", ".js")):
        # Файл с версией в адресе не меняется никогда: новая версия = новый адрес
        response.headers["Cache-Control"] = (
            "public, max-age=31536000, immutable" if "v" in request.query_params else "no-cache"
        )
    elif path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.get("/", include_in_schema=False)
@app.get("/index.html", include_in_schema=False)
def index() -> HTMLResponse:
    # Страница всегда перезапрашивается, а через неё подтягиваются свежие адреса статики
    return HTMLResponse(
        render_index(), headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
    )


app.mount("/", StaticFiles(directory=ROOT / "frontend", html=True), name="frontend")
