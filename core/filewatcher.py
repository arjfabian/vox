import asyncio
from core.logger import log_info, log_warn

class AgentFileWatcher:
    """
    Polls the filesystem for changes to any agent's tracked files.

    Monitored per agent directory:
      - agent.yml
      - .env
      - roles/*.py

    On change detected → calls orchestrator.restart_agent(agent_name).
    Uses SHA-256 content hashing (not mtime) so moves/copies are also caught.
    Poll interval defaults to 2 seconds — low enough for dev, cheap enough
    for a VPS.
    """

    POLL_INTERVAL = 2.0  # seconds

    def __init__(self, agents_dir: Path, orchestrator: "VOXOrchestrator"):
        self._agents_dir  = agents_dir
        self._orchestrator = orchestrator
        self._hashes: Dict[str, str] = {}   # file_path_str → sha256_hex
        self._running = False

    def _hash_file(self, path: Path) -> str:
        """Returns the SHA-256 hex digest of a file, or '' on read error."""
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except Exception:
            return ""

    def _snapshot(self) -> Dict[str, str]:
        """Builds a {path_str: hash} map for all monitored files."""
        result: Dict[str, str] = {}
        for agent_dir in self._agents_dir.iterdir():
            if not agent_dir.is_dir() or agent_dir.name.startswith("."):
                continue
            for target in [agent_dir / "agent.yml", agent_dir / ".env"]:
                if target.exists():
                    result[str(target)] = self._hash_file(target)
            roles_dir = agent_dir / "roles"
            if roles_dir.exists():
                for py_file in roles_dir.glob("*.py"):
                    if  not py_file.name.startswith("__") \
                    and not py_file.name.endswith("_test.py"):
                        result[str(py_file)] = self._hash_file(py_file)
        return result

    def _changed_agents(self, old: Dict[str, str], new: Dict[str, str]) -> Set[str]:
        """
        Compares two snapshots and returns the set of agent *directory names*
        whose files changed (added, modified, or removed).
        """
        changed: Set[str] = set()
        all_keys = set(old) | set(new)
        for key in all_keys:
            if old.get(key) != new.get(key):
                # key is an absolute path — second part after agents_dir is
                # the agent folder name
                rel = Path(key).relative_to(self._agents_dir)
                changed.add(rel.parts[0])
        return changed

    async def run(self):
        """Main polling loop. Runs as a background asyncio task."""
        self._running = True
        self._hashes  = self._snapshot()
        log_info("FileWatcher active. Monitoring all agent directories.")

        while self._running:
            await asyncio.sleep(self.POLL_INTERVAL)
            new_hashes = self._snapshot()
            changed    = self._changed_agents(self._hashes, new_hashes)

            for agent_name in changed:
                log_warn(f"Change detected in agent '{agent_name}'. Triggering restart...")
                await self._orchestrator.restart_agent(agent_name)

            self._hashes = new_hashes

    def stop(self):
        self._running = False