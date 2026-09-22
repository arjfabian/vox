"""OllamaAdapter — local Ollama backend for the ai.llm port.

Preserves the provider behavior previously implemented directly in the ai.llm
capability: the Ollama ``/api/chat`` completion endpoint with streaming off,
JSON mode via ``format: json``, ``num_predict``/``temperature`` generation
options, and base64 image references for vision inference.

The endpoint, model defaults, request shape and timeout are provider-specific
and stay inside this adapter. Configuration comes from ``config.yml``,
aggregated into the ai.llm contract, never from environment variables.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import asdict

import httpx

from ...models import LLMChatMessage, LLMGenerationResult
from ..base import LLMAdapter

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://localhost:11434"
_DEFAULT_MODEL = "llama3.2:1b"
_DEFAULT_VISION_MODEL = "llava"
_DEFAULT_TIMEOUT = 60.0


class OllamaAdapter(LLMAdapter):
    ADAPTER_ID = "ollama"

    def __init__(self, config: dict) -> None:
        self._base_url = str(
            config.get("OLLAMA_API_BASE_URL", _DEFAULT_BASE_URL)
        ).rstrip("/")
        self._model = str(config.get("OLLAMA_MODEL", _DEFAULT_MODEL))
        self._vision_model = str(
            config.get("OLLAMA_VISION_MODEL", _DEFAULT_VISION_MODEL)
        )
        self._timeout = float(config.get("OLLAMA_TIMEOUT", _DEFAULT_TIMEOUT))
        self._client: httpx.AsyncClient | None = None

    # -- availability ---------------------------------------------------------

    @classmethod
    def is_configured(cls, config: dict) -> bool:
        # The local Ollama server needs no secret; a default endpoint is always
        # reachable at mount time.
        return True

    # -- model selection (adapter-owned defaults) ------------------------------

    def resolve_model(self, model: str | None) -> str:
        return model or self._model

    def _resolve_vision_model(self, model: str | None) -> str:
        return model or self._vision_model

    # -- lifecycle -------------------------------------------------------------

    async def start(self) -> None:
        await self._ensure_client()

    async def shutdown(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- provider RPC ----------------------------------------------------------

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def generate(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
    ) -> LLMGenerationResult:
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: dict = {
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

        return await self._chat(payload)

    async def chat(
        self,
        *,
        model: str | None,
        messages: list[LLMChatMessage],
        temperature: float,
        max_tokens: int,
    ) -> LLMGenerationResult:
        payload = {
            "model": model or self._model,
            "messages": [asdict(message) for message in messages],
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        return await self._chat(payload)

    async def generate_vision(
        self,
        *,
        model: str | None,
        system: str,
        prompt: str,
        image_bytes: bytes,
        temperature: float,
        max_tokens: int,
    ) -> LLMGenerationResult:
        image_b64 = base64.b64encode(image_bytes).decode()
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt, "images": [image_b64]},
        ]
        payload = {
            "model": model or self._vision_model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        return await self._chat(payload)

    async def _chat(self, payload: dict) -> LLMGenerationResult:
        client = await self._ensure_client()
        response = await client.post(f"{self._base_url}/api/chat", json=payload)
        response.raise_for_status()
        data = response.json()
        content = data.get("message", {}).get("content", "").strip()
        return LLMGenerationResult(
            content=content,
            model=str(payload["model"]),
            tokens_generated=data.get("eval_count", 0),
            raw=data,
        )