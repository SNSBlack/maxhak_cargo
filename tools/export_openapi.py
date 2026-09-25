"""Выгружает схему OpenAPI в docs/openapi.yaml и docs/openapi.json.

Схема требуется как часть сдачи: у решения есть собственное HTTP API, которым
пользуется мини-приложение. Файл генерируется из кода, а не пишется руками,
поэтому не расходится с реальными методами.

    python tools/export_openapi.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.main import app  # noqa: E402


def main() -> None:
    schema = app.openapi()
    out_dir = ROOT / "docs"
    out_dir.mkdir(exist_ok=True)

    (out_dir / "openapi.json").write_text(
        json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    (out_dir / "openapi.yaml").write_text(
        yaml.safe_dump(schema, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8",
        newline="\n",
    )

    methods = sum(
        1 for path in schema["paths"].values() for verb in path if verb in {"get", "post", "put", "delete"}
    )
    print(f"OpenAPI {schema['openapi']}, версия API {schema['info']['version']}")
    print(f"Путей: {len(schema['paths'])}, методов: {methods}")
    print(f"Записано: {out_dir / 'openapi.yaml'} и {out_dir / 'openapi.json'}")


if __name__ == "__main__":
    main()
