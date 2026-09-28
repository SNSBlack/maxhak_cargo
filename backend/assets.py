"""Версии статики мини-приложения для сброса кэша.

Браузер внутри MAX кэширует css и js по собственной эвристике, если сервер не
присылает Cache-Control, и после обновления показывал старую версию сайта.
Решение: к адресам файлов добавляется версия из хэша содержимого. Любая правка
даёт новый адрес, и закэшированный старый файл перестаёт использоваться.
"""

from __future__ import annotations

import hashlib

from .config import ROOT

FRONTEND = ROOT / "frontend"
VERSIONED = ("styles.css", "app.js")


def file_version(name: str) -> str:
    return hashlib.sha1((FRONTEND / name).read_bytes()).hexdigest()[:10]


def build_id() -> str:
    """Общая версия фронтенда: меняется при правке любого из файлов."""
    parts = "".join(file_version(n) for n in ("index.html", *VERSIONED))
    return hashlib.sha1(parts.encode()).hexdigest()[:10]


def render_index() -> str:
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    html = html.replace('href="styles.css"', f'href="styles.css?v={file_version("styles.css")}"')
    html = html.replace('src="app.js"', f'src="app.js?v={file_version("app.js")}"')
    return html
