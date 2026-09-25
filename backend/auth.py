"""Проверка подписи initData мини-приложения MAX.

Клиент кладёт `window.WebApp.initData` в заголовок `X-Max-Init-Data`, backend
проверяет подпись и отклоняет запросы, сделанные вне сессии мини-приложения.

Точная схема вывода секрета в публичной документации MAX не расписана, поэтому
проверяются несколько известных вариантов HMAC-SHA256 (в том числе схема,
совпадающая с Telegram WebApp). Как только оператор платформы подтвердит
схему, лишние кандидаты надо удалить: сейчас они расширяют поверхность приёма.

Режимы (AUTH_MODE):
  strict  - без валидной подписи запрос отклоняется (прод, обязателен к модерации)
  lenient - запрос проходит, но помечается verified=False (пилот, отладка)
  dev     - подпись не проверяется (локальная разработка)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl

from fastapi import Header, HTTPException

from .config import settings

SIGNATURE_KEYS = {"hash", "sign", "signature"}


@dataclass
class WebAppUser:
    user_id: int | None
    name: str
    verified: bool
    raw: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"user_id": self.user_id, "name": self.name, "verified": self.verified}


def parse_init_data(raw: str) -> dict[str, str]:
    return dict(parse_qsl(raw, keep_blank_values=True))


def data_check_string(pairs: dict[str, str]) -> str:
    return "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs) if k not in SIGNATURE_KEYS)


def _secret_candidates(token: str) -> list[bytes]:
    return [
        hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest(),
        hashlib.sha256(token.encode()).digest(),
        token.encode(),
    ]


def signature_matches(raw: str, token: str) -> bool:
    pairs = parse_init_data(raw)
    provided = next((pairs[k] for k in SIGNATURE_KEYS if k in pairs), None)
    if not provided or not token:
        return False
    message = data_check_string(pairs).encode()
    for secret in _secret_candidates(token):
        digest = hmac.new(secret, message, hashlib.sha256).digest()
        if hmac.compare_digest(digest.hex(), provided.lower()):
            return True
        if hmac.compare_digest(base64.b64encode(digest).decode(), provided):
            return True
    return False


def is_fresh(pairs: dict[str, str], ttl_hours: int) -> bool:
    auth_date = pairs.get("auth_date")
    if not auth_date:
        return True  # поле опционально, срок жизни тогда не проверяем
    try:
        return (time.time() - int(auth_date)) <= ttl_hours * 3600
    except ValueError:
        return False


def extract_user(pairs: dict[str, str]) -> tuple[int | None, str]:
    blob = pairs.get("user") or pairs.get("user_info")
    if blob:
        try:
            data = json.loads(blob)
            name = " ".join(
                str(data.get(k, "")).strip()
                for k in ("first_name", "last_name")
                if data.get(k)
            ).strip()
            return data.get("id") or data.get("user_id"), name or "Пользователь MAX"
        except json.JSONDecodeError:
            pass
    uid = pairs.get("user_id")
    try:
        return int(uid) if uid else None, pairs.get("username") or "Пользователь MAX"
    except ValueError:
        return None, "Пользователь MAX"


def authenticate(raw: str | None) -> WebAppUser:
    mode = settings.auth_mode.lower()

    if not raw:
        if mode == "strict":
            raise HTTPException(401, "Нет initData: приложение открыто вне MAX")
        return WebAppUser(None, "Локальный режим", False, {})

    pairs = parse_init_data(raw)
    user_id, name = extract_user(pairs)

    if mode == "dev":
        return WebAppUser(user_id, name, False, pairs)

    verified = signature_matches(raw, settings.max_bot_token) and is_fresh(
        pairs, settings.session_ttl_hours
    )
    if not verified and mode == "strict":
        raise HTTPException(401, "Подпись initData не прошла проверку")
    return WebAppUser(user_id, name, verified, pairs)


async def current_user(x_max_init_data: str | None = Header(default=None)) -> WebAppUser:
    return authenticate(x_max_init_data)
