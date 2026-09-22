"""GeminiAdapter — Google Gemini backend for the ai.llm port.

Provider-internal details live here: the Google AI Studio ``generateContent``
endpoint, ``x-goog-api-key`` authentication, system-instruction and
inline-image request shape, ``responseMimeType`` JSON mode, and the
``gemini-2.5-flash`` model defaults.

The credential (``GEMINI_API_KEY``) is declared as a secret in the adapter's
``config.yml`` and is injected only through the bound workload's Vault secrets;
there is no environment-variable or ``.env`` fallback. Without an injected key
the adapter is simply not available for that workload (``is_configured``).
"""

from __future__ import annotations

import base64
import logging

import httpx

from ...models import LLMChatMessage, LLMGenerationResult
from ..base import LLMAdapter

logger = logging.getLogger(__name__)

_GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta"

_DEFAULT_MODEL = "gemini-2.5-flash"
_DEFAULT_VISION_MODEL = "gemini-2.5-flash"
_DEFAULT_TIMEOUT = 60.0

_MAGIC_TO_MIME = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def _sniff_mime(image_bytes: bytes) -> str:
    """Best-effort raster MIME detection to satisfy Gemini's inline_data."""
    for magic, mime in _MAGIC_TO_MIME:
        if image_bytes.startswith(magic):
            return mime
    if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    if image_bytes[:2] == b"BM":
        return "image/bmp"
    return "image/jpeg"


class GeminiAdapter(LLMAdapter):
    ADAPTER_ID = "gemini"

    def __init__(self, config: dict) -> None:
        self._api_key = str(config.get("GEMINI_API_KEY", ""))
        self._model = str(config.get("GEMINI_MODEL", _DEFAULT_MODEL))
        self._vision_model = str(
            config.get("GEMINI_VISION_MODEL", _DEFAULT_VISION_MODEL)
        )
        self._timeout = float(config.get("GEMINI_TIMEOUT", _DEFAULT_TIMEOUT))
        self._client: httpx.AsyncClient | None = None

    # -- availability ---------------------------------------------------------

    @classmethod
    def is_configured(cls, config: dict) -> bool:
        # Credential-gated: the adapter is available only when the workload's
        # Vault injection provided an API key. Never enabled by environment.
        return bool(config.get("GEMINI_API_KEY"))

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
        return await self._generate_content(
            model=model,
            system=system,
            contents=[{"role": "user", "parts": [{"text": prompt}]}],
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
        )

    async def chat(
        self,
        *,
        model: str | None,
        messages: list[LLMChatMessage],
        temperature: float,
        max_tokens: int,
    ) -> LLMGenerationResult:
        effective = model or self._model
        system_parts = [m.content for m in messages if m.role == "system"]
        system = "\n".join(system_parts).strip()

        contents: list[dict] = []
        for message in messages:
            if message.role == "system":
                continue
            role = "model" if message.role == "assistant" else "user"
            parts: list[dict] = []
            if message.content:
                parts.append({"text": message.content})
            for image in message.images:
                parts.append(
                    {"inline_data": {"mime_type": "image/jpeg", "data": image}}
                )
            contents.append({"role": role, "parts": parts})

        return await self._generate_content(
            model=effective,
            system=system,
            contents=contents,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=False,
        )

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
        effective = model or self._vision_model
        mime = _sniff_mime(image_bytes)
        data = base64.b64encode(image_bytes).decode()
        contents = [
            {
                "role": "user",
                "parts": [
                    {"inline_data": {"mime_type": mime, "data": data}},
                    {"text": prompt},
                ],
            }
        ]
        return await self._generate_content(
            model=effective,
            system=system,
            contents=contents,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=False,
        )

    async def _generate_content(
        self,
        *,
        model: str,
        system: str,
        contents: list[dict],
        temperature: float,
        max_tokens: int,
        json_mode: bool,
    ) -> LLMGenerationResult:
        client = await self._ensure_client()

        generation_config: dict = {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        }
        if json_mode:
            generation_config["responseMimeType"] = "application/json"

        body: dict = {
            "contents": contents,
            "generationConfig": generation_config,
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}

        response = await client.post(
            f"{_GEMINI_ENDPOINT}/models/{model}:generateContent",
            json=body,
            headers={"x-goog-api-key": self._api_key},
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError:
            # Diagnostic-only: surface Google's error payload (status, URL,
            # response body) without ever logging the credential, request
            # headers, or request body. The original exception is re-raised
            # unchanged so its traceback is preserved for callers.
            logger.exception(
                "Gemini API HTTP %s calling %s: %s",
                response.status_code,
                response.request.url,
                response.text[:2000],
            )
            raise
        data = response.json()

        try:
            parts = data["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError) as exc:
            raise RuntimeError(
                f"Gemini response missing candidate content: {data}"
            ) from exc

        content = "".join(part.get("text", "") for part in parts).strip()
        return LLMGenerationResult(
            content=content,
            model=model,
            tokens_generated=data.get("usageMetadata", {}).get(
                "totalTokenCount", 0
            ),
            raw=data,
        )