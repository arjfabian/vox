from pydantic import BaseModel, Field
from typing import Optional, List
import httpx

class OllamaConfig(BaseModel):
    host: str = "http://localhost:11434"
    model: str = "llama3:latest"
    temperature: float = Field(default=0.7, ge=0.0, le=1.0)
    num_predict: int = Field(default=256, gt=0)
    timeout: float = 60.0

class OllamaCapability:
    def __init__(self, agent, **kwargs):
        self.agent = agent
        self.config = OllamaConfig(**kwargs)
        self.base_url = self.config.host.rstrip('/')
        self.chat_url = f"{self.base_url}/api/chat"
        self.tags_url = f"{self.base_url}/api/tags"

    async def check_health(self) -> bool:
        """
        Checks that Ollama is online and the model is available.        
        """
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(self.tags_url)
                if response.status_code == 200:
                    models = [m['name'] for m in response.json().get("models", [])]
                    # Supports exact matches or tags (ej: llama3 vs llama3:latest)
                    exists = any(m == self.config.model or m.startswith(f"{self.config.model}:") for m in models)
                    if not exists:
                        self.agent.log_agent_warn(f"Model '{self.config.model}' not found in Ollama tags.")
                    return exists
        except Exception as e:
            self.agent.log_agent_fail(f"Ollama Health Check failed: {e}")
        return False

    async def generate_response(self, system_prompt: str, user_prompt: str) -> str:
        """Queries Ollama Chat API."""
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "stream": False,
            "options": {"temperature": self.config.temperature, "num_predict": self.config.num_predict},
            "format": "json" if "json" in system_prompt.lower() else ""
        }

        try:
            async with httpx.AsyncClient(timeout=self.config.timeout) as client:
                response = await client.post(self.chat_url, json=payload)
                response.raise_for_status()
                return response.json().get("message", {}).get("content", "").strip()
        except Exception as e:
            self.agent.log_agent_fail(f"Ollama Generation Error: {e}")
            return ""