"""
VOX Capability Base Class
Internal Name: THE TOOL

Defines the interface for all external integrations (Capabilities).
Capabilities act as bridge modules that provide specific technical 
functionalities (AI, Messaging, Database) to an Agent's Roles.
"""

import inspect
from typing import Any, Dict, List, Tuple, Optional
from core.logger import log_warn

class VOXCapability:
    """
    Base class for system-wide capabilities.
    Implements a strict parameter validation protocol and lifecycle hooks.
    """

    # Blueprint for required configuration.
    # Format: { "KEY": ["Description", default_value_or_None] }
    PARAMS: Dict[str, List[Any]] = {}

    @classmethod
    def get_params(cls) -> List[str]:
        """
        Returns the list of mandatory and optional keys defined in the manifest.
        """
        return list(cls.PARAMS.keys())
        
    @classmethod
    def explain_config(cls) -> str:
        """
        Generates a human-readable manifest of the configuration requirements.
        Useful for provisioning new agents and debugging environment gaps.
        """
        header = f" Requirements for {cls.__name__} "
        output = [f"\n{header:=^60}"]
        
        for param, (desc, default) in cls.PARAMS.items():
            status = f"[Default: {default}]" if default is not None else "[REQUIRED]"
            output.append(f"  • {param:<22} | {desc:<35} {status}")
            
        output.append("=" * 60)
        return "\n".join(output)

    def __init__(self):
        """
        Initializes the capability with its scoped configuration.
        
        :param agent: Reference to the hosting VOXAgent.
        :param kwargs: Validated parameters extracted via validate_and_extract.
        """
        self.name = self.__class__.__name__

    @classmethod
    def validate_and_extract(cls, config: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
        """
        Filters and validates incoming configuration against the Capability's requirements.
        
        :param config: The raw dictionary (usually from Agent config/secrets).
        :return: A tuple containing (sanitized_parameters, list_of_missing_required_keys).
        """
        extracted = {}
        missing = []

        for param, details in cls.PARAMS.items():
            # Schema: [0] Description, [1] Default Value
            _, default = details
            
            # Extract value from the provided configuration pool
            value = config.get(param)
            
            # Validation logic: Fallback to default or mark as missing
            if value is None:
                if default is not None:
                    value = default
                else:
                    missing.append(param)
            
            extracted[param] = value
            
        return extracted, missing

    def mount(self, agent: Any, config: Dict[str, Any]):
        """
        Agent-specific binding.
        Returns a 'BoundCapability' or configures a proxy.
        """
        # Validate the config provided by the agent against our PARAMS
        sanitized, missing = self.validate_and_extract(config)
        if missing:
            raise ValueError(f"Missing params for {self.name}: {missing}")
        # Return a Proxy or a wrapper that knows about the agent AND the config
        return BoundCapability(self, agent, sanitized)

    async def health_check(self) -> bool:
        """Override this to verify service availability (Ollama, API keys, etc)."""
        return True



class BoundCapability:
    def __init__(self, capability, agent, params):
        self._capability = capability
        self._agent = agent
        self._params = params
        self.id = getattr(capability, "id", "unknown.capability")

    def __getattr__(self, name):
        # 1. Is this a parameter (e.g. OLLAMA_URL for Ollama)?
        if name in self._params:
            return self._params[name]

        # 2. Search for the attribute/method in the global Capability
        try:
            attr = getattr(self._capability, name)
        except AttributeError:
            raise AttributeError(f"Capability [{self.id}] has no attribute '{name}'")

        if callable(attr):
            bound_self = self
            raw = getattr(type(self._capability), name, None)
            func = raw if raw is not None else attr

            if inspect.iscoroutinefunction(func):
                async def wrapper(*args, **kwargs):
                    return await func(bound_self, *args, **kwargs)
            else:
                def wrapper(*args, **kwargs):
                    return func(bound_self, *args, **kwargs)
            return wrapper