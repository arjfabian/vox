"""ai.llm — async HTTP client for LLM backends.

Provider-agnostic client wrapping the Ollama /chat API (OpenAI-compatible
endpoints). Connection pool is allocated at boot time and reused across all
requests.
"""

import logging
from dataclasses import asdict

import httpx

from .models import LLMChatRequest, LLMGenerationResult

logger = logging.getLogger(__name__)


class LLMClient:
    def __init__(self, base_url: str, timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def is_healthy(self) -> bool:
        try:
            response = await self._client.get(f"{self.base_url}/api/tags")
            return response.status_code == 200
        except Exception as e:  # noqa: BLE001 — health check must not raise
            logger.warning("LLM health check failed: %s", e)
            return False

    async def chat(self, request: LLMChatRequest) -> LLMGenerationResult:
        payload = asdict(request)
        response = await self._client.post(
            f"{self.base_url}/api/chat",
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        content = data.get("message", {}).get("content", "").strip()
        return LLMGenerationResult(
            content=content,
            model=request.model,
            tokens_generated=data.get("eval_count", 0),
            raw=data,
        )

    async def generate(
        self,
        model: str,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        json_mode: bool = False,
    ) -> LLMGenerationResult:
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        if json_mode:
            payload["format"] = "json"

        response = await self._client.post(
            f"{self.base_url}/api/chat",
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        content = data.get("message", {}).get("content", "").strip()
        return LLMGenerationResult(
            content=content,
            model=model,
            tokens_generated=data.get("eval_count", 0),
            raw=data,
        )
