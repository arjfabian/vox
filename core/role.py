"""
VOX Role Abstract Base Class
Internal Name: THE BEHAVIOR

Defines the behavioral contract for all Agent Roles.
Roles act as specialized event listeners that execute logic based on 
specific triggers (events) and expose functional capabilities.
"""

import weakref
import inspect
from typing import Any, Dict, Optional, Callable, Awaitable

class AgentHostDeadError(Exception):
    """Raised when a Role tries to access its parent Agent after it has been garbage collected."""
    pass

class VOXRole:
    """
    Base class for Agent Behaviors.
    Implements a decorator-based event registration system and dynamic discovery.
    """

    def __init__(self, agent: Any):
        """
        Initializes the Role with a weak reference to its host.
        :param agent: The VOXAgent instance that owns this role.
        """
        # Weakref prevents circular dependency memory leaks
        self._agent_ref = weakref.ref(agent)
        self._handlers: Dict[str, Callable[..., Awaitable[None]]] = {}
    
    @property
    def agent(self) -> Any:
        """
        Thread-safe access to the host Agent.
        :raises AgentHostDeadError: If the parent Agent is no longer in memory.
        """
        instance = self._agent_ref()
        if instance is None:
            raise AgentHostDeadError("Parent Agent host has been terminated or collected.")
        return instance

    def on(self, event_name: str):
        """
        High-level decorator to register asynchronous event handlers.
        Usage: 
            @self.on("inbound_message")
            async def my_method(self, content): ...
        """
        def decorator(func: Callable[..., Awaitable[None]]):
            self._handlers[event_name] = func
            return func
        return decorator

    async def handle_event(self, event_name: str, **kwargs) -> bool:
        """
        Event Execution Engine.
        Executes the registered handler for a given event if it exists.
        
        :param event_name: The slug of the event to process.
        :param kwargs: Sanitized data passed by the Agent's emitter.
        :return: True if the event was handled, False otherwise (Stop Propagation support).
        """
        if event_name in self._handlers:
            # Execute the handler with provided context
            await self._handlers[event_name](**kwargs)
            return True
        return False

    def get_capabilities(self) -> Dict[str, str]:
        """
        Self-Introspection Protocol.
        Scans the Role's public API to expose available commands to the Agent.
        
        :return: A dictionary mapping method names to their docstring descriptions.
        """
        capabilities = {}
        
        # Reserved words that should not be exposed as public Agent commands
        RESERVED = {'handle_event', 'on', 'get_capabilities', 'agent'}

        for name in dir(self):
            # Filtering: No private/protected members, no dunders, no reserved keywords
            if name.startswith('_') or name in RESERVED:
                continue
                
            attr = getattr(self, name)
            
            # Identify valid actions: Must be a method or an async coroutine function
            if inspect.ismethod(attr) or inspect.iscoroutinefunction(attr):
                # Use docstring for NLU/Help context, fallback to generic description
                description = inspect.getdoc(attr) or "Autonomous action available in VOX."
                capabilities[name] = description
                
        return capabilities