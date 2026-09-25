"""Тонкий клиент MAX Bot API.

Формат запросов сверен с живым API (botapi.max.ru) 2026-09-24:
  - авторизация: заголовок `Authorization: <token>` без префикса Bearer;
    query-параметр access_token помечен как deprecated и отклоняется;
  - отправка: POST /messages?user_id=<id> | ?chat_id=<id>;
  - клавиатура: вложение `inline_keyboard`, поле payload.buttons - список строк
    кнопок (список списков);
  - кнопка мини-приложения: {"type": "open_app", "text": ..., "web_app": "<строка>"}.
    Поле web_app принимает именно строку: объект вида {"url": ...} API не
    десериализует;
  - long polling: GET /updates?marker=&limit=&timeout=;
  - ответ на callback: POST /answers?callback_id=<id>.
"""

from __future__ import annotations

from typing import Any

import httpx

from .config import settings


class MaxApiError(RuntimeError):
    pass


def callback_button(text: str, payload: str) -> dict[str, Any]:
    return {"type": "callback", "text": text, "payload": payload}


def link_button(text: str, url: str) -> dict[str, Any]:
    return {"type": "link", "text": text, "url": url}


def open_app_button(text: str, web_app: str) -> dict[str, Any]:
    """Кнопка запуска мини-приложения. web_app - строка (URL или диплинк бота)."""
    return {"type": "open_app", "text": text, "web_app": web_app}


def keyboard(rows: list[list[dict[str, Any]]]) -> dict[str, Any]:
    return {"type": "inline_keyboard", "payload": {"buttons": rows}}


class MaxApi:
    def __init__(self, token: str | None = None, base_url: str | None = None) -> None:
        self.token = token or settings.max_bot_token
        if not self.token:
            raise MaxApiError("MAX_BOT_TOKEN не задан")
        self.base_url = (base_url or settings.max_api_base).rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": self.token, "Content-Type": "application/json"},
            timeout=httpx.Timeout(60.0, read=90.0),
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            resp = await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise MaxApiError(f"Сеть MAX API: {exc}") from exc
        data: dict[str, Any]
        try:
            data = resp.json()
        except ValueError:
            raise MaxApiError(f"MAX API вернул не JSON: {resp.status_code} {resp.text[:200]}")
        if resp.status_code >= 400 or data.get("code"):
            raise MaxApiError(f"MAX API {resp.status_code}: {data}")
        return data

    async def me(self) -> dict[str, Any]:
        return await self._request("GET", "/me")

    async def get_updates(
        self, marker: int | None = None, timeout: int = 30, limit: int = 100
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"timeout": timeout, "limit": limit}
        if marker is not None:
            params["marker"] = marker
        return await self._request("GET", "/updates", params=params)

    async def send_message(
        self,
        text: str,
        *,
        user_id: int | None = None,
        chat_id: int | None = None,
        buttons: list[list[dict[str, Any]]] | None = None,
        notify: bool = True,
    ) -> dict[str, Any]:
        if not (user_id or chat_id):
            raise MaxApiError("Нужен user_id или chat_id получателя")
        params = {"user_id": user_id} if user_id else {"chat_id": chat_id}
        body: dict[str, Any] = {"text": text, "notify": notify}
        if buttons:
            body["attachments"] = [keyboard(buttons)]
        return await self._request("POST", "/messages", params=params, json=body)

    async def answer_callback(
        self, callback_id: str, *, text: str | None = None, notification: str | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if text:
            body["message"] = {"text": text}
        if notification:
            body["notification"] = notification
        return await self._request(
            "POST", "/answers", params={"callback_id": callback_id}, json=body
        )
