"""Проверки раздачи мини-приложения. Запуск: python tests/test_web.py

Регрессия: браузер внутри MAX показывал старую версию сайта после обновления,
потому что сервер не присылал Cache-Control, а адреса css и js не менялись.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from backend import assets  # noqa: E402
from backend.main import app  # noqa: E402

# Без контекстного менеджера lifespan не запускается, и бот не стартует
client = TestClient(app)


def test_index_is_never_cached():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "no-store" in resp.headers.get("cache-control", ""), resp.headers.get("cache-control")


def test_assets_are_versioned_by_content():
    html = client.get("/").text
    css = re.search(r'href="styles\.css\?v=([0-9a-f]{10})"', html)
    js = re.search(r'src="app\.js\?v=([0-9a-f]{10})"', html)
    assert css and js, "у статики нет версии в адресе"
    assert css.group(1) == assets.file_version("styles.css")
    assert js.group(1) == assets.file_version("app.js")


def test_versioned_asset_cached_forever_unversioned_revalidated():
    v = assets.file_version("styles.css")
    versioned = client.get(f"/styles.css?v={v}")
    assert versioned.status_code == 200
    assert "immutable" in versioned.headers["cache-control"]
    bare = client.get("/styles.css")
    assert bare.headers["cache-control"] == "no-cache"


def test_api_is_not_cached():
    assert client.get("/api/health").headers["cache-control"] == "no-store"


def test_build_id_changes_with_content():
    before = assets.build_id()
    path = assets.FRONTEND / "styles.css"
    original = path.read_bytes()
    try:
        path.write_bytes(original + b"\n/* test */\n")
        assert assets.build_id() != before, "версия не изменилась после правки"
    finally:
        path.write_bytes(original)
    assert assets.build_id() == before


if __name__ == "__main__":
    failures = 0
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            try:
                func()
                print(f"ok   {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    print("\nпровалов:", failures)
    sys.exit(1 if failures else 0)
