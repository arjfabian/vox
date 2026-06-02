"""Ollama capability runtime.

Provides local LLM inference for VOX agents through Ollama.
One global Capability singleton with per-agent configuration handled by VOXBoundCapability.
"""

import base64

from vox.capabilities.base import VOXCapability

from .client import OllamaClient
from .models import OllamaChatRequest, OllamaMessage, OllamaOptions


class OllamaCapability(VOXCapability):

    CAPABILITY_NAME = "ollama"

    PARAMS = {
        "OLLAMA_URL": ["Ollama server endpoint", "http://localhost:11434"],
        "OLLAMA_MODEL": ["Primary language model", "llama3.2:3b"],
        "OLLAMA_VISION_MODEL": ["Primary vision model", "llava"],
        "OLLAMA_TEMPERATURE": ["Generation creativity (0.0 - 1.0)", 0.7],
        "OLLAMA_NUM_PREDICT": ["Maximum generated tokens", 256],
        "OLLAMA_TIMEOUT": ["Inference timeout in seconds", 60.0],
    }

    def _build_client(self) -> OllamaClient:
        return OllamaClient(
            base_url=self.OLLAMA_URL,
            timeout=float(self.OLLAMA_TIMEOUT),
        )

    def _build_options(self) -> OllamaOptions:
        return OllamaOptions(
            temperature=float(self.OLLAMA_TEMPERATURE),
            num_predict=int(self.OLLAMA_NUM_PREDICT),
        )

    @classmethod
    async def health_check(cls) -> bool:
        client = OllamaClient(
            base_url=cls.PARAMS["OLLAMA_URL"][1],
            timeout=5.0,
        )
        return await client.is_healthy()

    async def generate(
        self, system: str, prompt: str, *, json_mode: bool = False
    ) -> str:
        request = OllamaChatRequest(
            model=self.OLLAMA_MODEL,
            messages=[
                OllamaMessage(role="system", content=system),
                OllamaMessage(role="user", content=prompt),
            ],
            stream=False,
            format="json" if json_mode else None,
            options=self._build_options(),
        )
        client = self._build_client()
        response = await client.chat(request)
        return response.content

    async def generate_vision(
        self, system: str, prompt: str, image_path: str
    ) -> str:
        self.log(f"Generating vision response using [{self.OLLAMA_VISION_MODEL}]")
        with open(image_path, "rb") as file:
            image_b64 = base64.b64encode(file.read()).decode()
        request = OllamaChatRequest(
            model=self.OLLAMA_VISION_MODEL,
            messages=[
                OllamaMessage(role="system", content=system),
                OllamaMessage(role="user", content=prompt, images=[image_b64]),
            ],
            stream=False,
        )
        client = self._build_client()
        response = await client.chat(request)
        return response.content
