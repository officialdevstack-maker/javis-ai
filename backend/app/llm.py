import json
from collections.abc import AsyncIterator
from typing import Protocol

import httpx

from .config import get_settings
from .schemas import Message, Scope


class ChatProvider(Protocol):
    async def stream_response(
        self,
        client: httpx.AsyncClient,
        payload: dict[str, object],
        *,
        scope: Scope,
    ) -> AsyncIterator[dict[str, object]]: ...


class OllamaError(Exception):
    """Raised when Ollama cannot provide a valid chat response."""


class OllamaProvider:
    async def stream_response(
        self,
        client: httpx.AsyncClient,
        payload: dict[str, object],
        *,
        scope: Scope,
    ) -> AsyncIterator[dict[str, object]]:
        del scope
        settings = get_settings()
        async with client.stream(
            "POST",
            f"{settings.ollama_url}/api/chat",
            json=payload,
        ) as response:
            if response.status_code != 200:
                body = (await response.aread()).decode(errors="replace")
                raise OllamaError(
                    f"Ollama returned HTTP {response.status_code}: {body}"
                )

            async for line in response.aiter_lines():
                if not line:
                    continue
                try:
                    data: object = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise OllamaError("Ollama returned invalid JSON") from exc
                if not isinstance(data, dict):
                    raise OllamaError("Ollama returned an invalid streaming response")
                if "error" in data:
                    raise OllamaError(str(data["error"]))
                yield data
                if data.get("done") is True:
                    break


def get_chat_provider() -> ChatProvider:
    return OllamaProvider()


async def stream_chat(
    messages: list[Message],
    *,
    scope: Scope = "private",
) -> AsyncIterator[str]:
    settings = get_settings()
    payload: dict[str, object] = {
        "model": settings.model_for_task("chat"),
        "messages": [message.model_dump() for message in messages],
        "stream": True,
    }
    timeout = httpx.Timeout(None, connect=10.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        async for data in get_chat_provider().stream_response(
            client,
            payload,
            scope=scope,
        ):
            message = data.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str) and content:
                    yield content


async def check_ollama() -> tuple[bool, bool]:
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f"{settings.ollama_url}/api/tags")
            response.raise_for_status()
            tags: object = response.json()
    except (httpx.HTTPError, ValueError):
        return False, False

    if not isinstance(tags, dict):
        return True, False
    models = tags.get("models")
    if not isinstance(models, list):
        return True, False

    wanted_models = {
        model if ":" in model else f"{model}:latest"
        for model in (
            settings.model_for_task("chat"),
            settings.model_for_task("planning"),
            settings.model_for_task("code"),
        )
    }
    names = [
        model.get("name")
        for model in models
        if isinstance(model, dict) and isinstance(model.get("name"), str)
    ]
    return True, wanted_models.issubset(names)
