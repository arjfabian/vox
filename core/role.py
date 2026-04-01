import weakref
import inspect

class AgentHostDeadError(Exception):
    pass

class VOXRole:
    def __init__(self, agent):
        self._agent_ref = weakref.ref(agent)
        self._handlers = {}
    
    @property
    def agent(self):
        inst = self._agent_ref()
        if inst is None:
            raise RuntimeError("Agent Host Dead")
            # TODO: Report to the War Room
        return inst

    def on(self, event_name):
        """Decorator to register handlers for the Role's events."""
        def decorator(func) :
            self._handlers[event_name] = func
            return func
        return decorator

    async def handle_event(self, event_name, **kwargs):
        if event_name in self._handlers:
            await self._handlers[event_name](**kwargs)

    def get_capabilities(self):
        """Scans for public methods."""
        caps = {}
        # Get all the object's attributes
        for name in dir(self):
            # Filter: no private methods, no dunders, no core
            if not name.startswith('_') and name not in ['handle_event', 'on', 'get_capabilities', 'agent']:
                method = getattr(self, name)
                # Verify that it is callable (async method or function)
                if inspect.ismethod(method) or inspect.iscoroutinefunction(method):
                    caps[name] = inspect.getdoc(method) or "Action available in VOX."
        return caps
        