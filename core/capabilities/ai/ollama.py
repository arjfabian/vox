"""
Ollama Capability Module

Provides local LLM inference services via the Ollama API. 
Supports health monitoring, dynamic parameter injection, and 
auto-detection of JSON mode for structured outputs.
"""

import httpx
from typing import Any, Dict, Optional
from core.capability import VOXCapability

class Capability(VOXCapability):
    """
    Standard interface for local model interaction.
    Implements high-level abstraction for chat generation and health diagnostics.
    """
    
    # Configuration Blueprint (Injected from Agent environment)
    PARAMS = {
        "OLLAMA_URL":         ["Ollama server endpoint",              "http://localhost:11434"],
        "OLLAMA_MODEL":       ["Target LLM model name (e.g. llama3)", "llama3:latest"],
        "OLLAMA_TEMPERATURE": ["Generation creativity (0.0 to 1.0)",  0.7],
        "OLLAMA_NUM_PREDICT": ["Maximum token generation limit",      256],
        "OLLAMA_TIMEOUT":     ["Request timeout in seconds",          60.0]
    }

    async def health_check(self) -> bool:
        """
        Global Health Check: Verifies if the Ollama service is up.
        Note: We don't check for a specific model here yet because this is global.
        """
        # We use a default or common URL for the global check
        default_url = self.PARAMS["OLLAMA_URL"][1].rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(f"{default_url}/api/tags")
                return response.status_code == 200
        except Exception:
            return False

    async def boot(self):
        # Connection is stateless — no persistent client needed.
        pass

    async def generate_response(self, system_prompt: str, user_prompt: str) -> str:
        base_url = self.OLLAMA_URL.rstrip("/") 
        chat_url = f"{base_url}/api/chat"
        
        payload = {
            "model": self.OLLAMA_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "stream": False,
            "options": {
                "temperature": float(self.OLLAMA_TEMPERATURE), 
                "num_predict": int(self.OLLAMA_NUM_PREDICT)
            },
            "format": "json" if "json" in system_prompt.lower() else ""
        }

        try:
            async with httpx.AsyncClient(timeout=float(self.OLLAMA_TIMEOUT)) as client:
                response = await client.post(chat_url, json=payload)
                response.raise_for_status()
                return response.json().get("message", {}).get("content", "").strip()
        except Exception as e:
            self._log_fail(f"Generation error: {e}")
            return ""

    def _log_fail(self, msg: str):
        from core.logger import log_fail
        log_fail(msg, "ai.ollama")