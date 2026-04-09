"""
VOX Capability Base Class
Internal Name: THE TOOL

Defines the interface for all external integrations (Capabilities).
Capabilities act as bridge modules that provide specific technical 
functionalities (AI, Messaging, Database) to an Agent's Roles.
"""

from typing import Any, Dict, List, Tuple, Optional

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

    def __init__(self, agent: Any, **kwargs):
        """
        Initializes the capability with its scoped configuration.
        
        :param agent: Reference to the hosting VOXAgent.
        :param kwargs: Validated parameters extracted via validate_and_extract.
        """
        self.agent = agent
        # The capability only "sees" the parameters it explicitly requested.
        self.params = kwargs

    async def boot(self):
        """
        Lifecycle Hook: Executed during Agent ignition.
        Use this for establishing persistent connections, sockets, or polling.
        """
        pass