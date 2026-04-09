"""
Ollama Capability Module

Provides local LLM inference services via the Ollama API. 
Supports health monitoring, dynamic parameter injection, and 
auto-detection of JSON mode for structured outputs.
"""

from core.capability import VOXCapability
import httpx
from typing import Any, Dict, Optional

class OllamaCapability(VOXCapability):
    """
    Standard interface for local model interaction.
    Implements high-level abstraction for chat generation and health diagnostics.
    """
    
    # Configuration Blueprint (Injected from Agent environment)
    PARAMS = {
        "OLLAMA_URL": ["Ollama server endpoint", "http://localhost:11434"],
        "OLLAMA_MODEL": ["Target LLM model name (e.g. llama3)", "llama3:latest"],
        "OLLAMA_TEMPERATURE": ["Generation creativity (0.0 to 1.0)", 0.7],
        "OLLAMA_NUM_PREDICT": ["Maximum token generation limit", 256],
        "OLLAMA_TIMEOUT": ["Request timeout in seconds", 60.0]
    }

    def __init__(self, agent: Any, **kwargs):
        """Initializes internal endpoint mappings and logs status."""
        super().__init__(agent, **kwargs)
        
        # Internal Endpoint Mapping
        base_url = self.params["OLLAMA_URL"].rstrip("/")
        self.chat_url = f"{base_url}/api/chat"
        self.tags_url = f"{base_url}/api/tags"
        
        self.agent.log_agent_info(f"Ollama provisioned with model: {self.params['OLLAMA_MODEL']}")

    async def check_health(self) -> bool:
        """
        Diagnostic protocol: Verifies server availability and model presence.
        :return: True if the model is loaded and ready for inference.
        """
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(self.tags_url)
                if response.status_code == 200:
                    models = [m['name'] for m in response.json().get("models", [])]
                    target = self.params["OLLAMA_MODEL"]
                    
                    # Fuzzy match for versioning (e.g. 'llama3' vs 'llama3:latest')
                    exists = any(m == target or m.startswith(f"{target}:") for m in models)
                    if not exists:
                        self.agent.log_agent_warn(f"Ollama Alert: Model '{target}' not found locally.")
                    return exists
        except Exception as e:
            self.agent.log_agent_fail(f"Ollama Connectivity Error: {e}")
        return False

    async def interact(self, user_prompt: str, system_context: Optional[str] = None) -> str:
        """
        High-level abstraction for Roles.
        Merges agent identity with user intent before generation.
        """
        # Fallback to default personality rules if no specific context is provided
        identity = system_context or self.agent.config.get("personality", {}).get("rules", "You are a VOX AI.")
        
        return await self.generate_response(
            system_prompt=identity,
            user_prompt=user_prompt
        )

    async def generate_response(self, system_prompt: str, user_prompt: str) -> str:
        """
        Low-level API interaction with the Ollama Chat completion engine.
        Implements dynamic JSON mode detection and error isolation.
        """
        payload = {
            "model": self.params["OLLAMA_MODEL"],
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "stream": False,
            "options": {
                "temperature": float(self.params["OLLAMA_TEMPERATURE"]), 
                "num_predict": int(self.params["OLLAMA_NUM_PREDICT"])
            },
            # Auto-enables JSON schema if requested in the system instructions
            "format": "json" if "json" in system_prompt.lower() else ""
        }

        try:
            timeout_val = float(self.params["OLLAMA_TIMEOUT"])
            async with httpx.AsyncClient(timeout=timeout_val) as client:
                response = await client.post(self.chat_url, json=payload)
                response.raise_for_status()
                
                # Extract content from the nested Ollama response structure
                content = response.json().get("message", {}).get("content", "").strip()
                return content

        except httpx.TimeoutException:
            self.agent.log_agent_fail(f"Ollama Timeout: Model took >{timeout_val}s to reply.")
        except Exception as e:
            self.agent.log_agent_fail(f"Ollama Generation Error: {e}")
            
        return ""