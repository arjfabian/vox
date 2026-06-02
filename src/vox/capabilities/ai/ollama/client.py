"""Async Ollama API client."""

import logging

from dataclasses import asdict

import httpx

from .models import OllamaChatRequest, OllamaChatResponse


logger = logging.getLogger(__name__)


class OllamaClient:

    def __init__(self, base_url: str, timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def is_healthy(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                return response.status_code == 200
        except Exception as e:
            logger.warning("Ollama health check failed: %s", e)
            return False

    async def chat(self, request: OllamaChatRequest) -> OllamaChatResponse:
        payload = asdict(request)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/api/chat", json=payload,
            )
            response.raise_for_status()
            data = response.json()
        content = data.get("message", {}).get("content", "").strip()
        return OllamaChatResponse(content=content, raw=data)
