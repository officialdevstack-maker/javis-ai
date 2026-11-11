import json
from collections.abc import AsyncIterator

import httpx

from .config import get_settings
from .schemas import Message


class OllamaError(Exception):
    """Raised when Ollama cannot provide a valid chat response."""


async def stream_chat(messages: list[Message]) -> AsyncIterator[str]:
    settings = get_settings()
    payload: dict[str, object] = {
        "model": settings.model,
        "messages": [message.model_dump() for message in messages],
        "stream": True,
    }
    timeout = httpx.Timeout(None, connect=10.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream(
            "POST",
            f"{settings.ollama_url}/api/chat",
            json=payload,
        ) as response:
            if response.status_code != 200:
                body = (await response.aread()).decode(errors="replace")
                raise OllamaError(f"Ollama returned HTTP {response.status_code}: {body}")

            async for line in response.aiter_lines():
                if not line:
                    continue
                data: object = json.loads(line)
                if not isinstance(data, dict):
                    raise OllamaError("Ollama returned an invalid streaming response")
                if "error" in data:
                    raise OllamaError(str(data["error"]))

                message = data.get("message")
                if isinstance(message, dict):
                    content = message.get("content")
                    if isinstance(content, str) and content:
                        yield content
                if data.get("done") is True:
                    break


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

    wanted_model = settings.model if ":" in settings.model else f"{settings.model}:latest"
    names = [
        model.get("name")
        for model in models
        if isinstance(model, dict) and isinstance(model.get("name"), str)
    ]
    return True, wanted_model in names
