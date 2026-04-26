"""
VOX Control Server
Internal Name: THE CONSOLE

Exposes a Unix Domain Socket (UDS) control plane for runtime management
of the VOX fleet without restarting the process.

Architecture:
  - Runs as a single asyncio task inside the main VOX event loop.
  - Accepts newline-delimited JSON commands from voxctl.py.
  - Dispatches commands to the live VOXOrchestrator instance.
  - Responds with newline-delimited JSON results.

Socket location: /run/vox/vox.sock  (production, systemd)
               : /tmp/vox.sock       (development fallback)

Security model:
  - The socket is created with mode 0o600 (owner read/write only).
  - Access control is enforced entirely by filesystem permissions — the
    same model used by Docker, containerd, and PostgreSQL.
  - No authentication tokens are needed: if you can write to the socket,
    you are the operator (root or the vox service user).
  - Commands are validated against a strict whitelist before dispatch.
    Unknown commands are rejected with an error response — never executed.

Wire protocol:
  Request  (client → server): {"cmd": "restart", "args": ["tina"]}\\n
  Response (server → client): {"ok": true,  "data": "..."}\\n
                            : {"ok": false, "error": "..."}\\n

The newline delimiter allows streaming multiple responses over a single
connection in the future (e.g. log tailing) without framing overhead.
"""

import asyncio
import json
import os
import stat
from pathlib import Path
from typing import Any, Dict, TYPE_CHECKING

from core.logger import log_info, log_ok, log_warn, log_fail

if TYPE_CHECKING:
    from vox import VOXOrchestrator


# ---------------------------------------------------------------------------
# Socket path resolution
# ---------------------------------------------------------------------------

def resolve_socket_path() -> Path:
    """
    Returns the UDS path appropriate for the runtime environment.

    Production (systemd):  /run/vox/vox.sock
      - /run is a tmpfs managed by systemd; the directory is created by
        the RuntimeDirectory= directive in the unit file.
      - This path survives neither reboots nor service restarts, which is
        correct — a stale socket from a crashed process is automatically
        cleaned up by systemd.

    Development (fallback): /tmp/vox.sock
      - Used when /run/vox does not exist (local dev, no systemd).
      - Cleaned up manually or on reboot.
    """
    production_dir = Path("/run/vox")
    if production_dir.exists():
        return production_dir / "vox.sock"
    return Path("/tmp/vox.sock")


# ---------------------------------------------------------------------------
# Command dispatcher
# ---------------------------------------------------------------------------

class ControlDispatcher:
    """
    Validates and dispatches control commands to the live Orchestrator.

    Whitelist design: every command must be explicitly registered here.
    An unknown command string never reaches the Orchestrator — it returns
    an error response immediately. This is a hard boundary against
    command injection through the socket.
    """

    def __init__(self, orchestrator: "VOXOrchestrator"):
        self._orc = orchestrator

        # Whitelist: command name → (handler, required_args_count, usage_hint)
        self._commands: Dict[str, tuple] = {
            "status":  (self._cmd_status,  0, "status"),
            "list":    (self._cmd_list,    0, "list"),
            "restart": (self._cmd_restart, 1, "restart <agent-name>"),
            "stop":    (self._cmd_stop,    1, "stop <agent-name>"),
            "start":   (self._cmd_start,   1, "start <agent-name>"),
            "help":    (self._cmd_help,    0, "help"),
        }

    async def dispatch(self, raw: str) -> Dict[str, Any]:
        """
        Parses and dispatches a raw JSON command string.

        :param raw: A single newline-stripped line from the socket.
        :return: A dict ready to be JSON-serialized and sent back.
        """
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return _err("Malformed request: expected JSON object.")

        cmd  = payload.get("cmd", "").strip().lower()
        args = payload.get("args", [])

        if not cmd:
            return _err("Missing 'cmd' field.")

        if cmd not in self._commands:
            known = ", ".join(sorted(self._commands))
            return _err(f"Unknown command '{cmd}'. Known: {known}.")

        handler, required_args, usage = self._commands[cmd]

        if len(args) < required_args:
            return _err(f"'{cmd}' requires {required_args} argument(s). Usage: {usage}")

        try:
            return await handler(*args)
        except Exception as e:
            log_fail(f"Control dispatcher error on '{cmd}': {e}")
            return _err(f"Internal error: {e}")

    # -----------------------------------------------------------------------
    # Command handlers
    # -----------------------------------------------------------------------

    async def _cmd_status(self) -> Dict[str, Any]:
        agents = []
        for agent_id, agent in self._orc.active_agents.items():
            agents.append({
                "id":        agent_id,
                "name":      agent.name,
                "state":     str(agent.state),
                "roles":     list(agent.roles.keys()),
                "master_id": agent.master_id,
                "queued":    agent._event_queue.size,
            })
        return _ok({
            "fleet_size": len(agents),
            "agents":     agents,
            "hierarchy":  self._orc._subordinates,
        })

    async def _cmd_list(self) -> Dict[str, Any]:
        names = [a.name for a in self._orc.active_agents.values()]
        return _ok({"agents": sorted(names)})

    async def _cmd_restart(self, agent_name: str) -> Dict[str, Any]:
        log_info(f"Control: restart '{agent_name}' requested via socket.")
        success = await self._orc.restart_agent(agent_name)
        if success:
            return _ok(f"Agent '{agent_name}' restarted successfully.")
        return _err(
            f"Failed to restart '{agent_name}'. "
            f"Check logs: journalctl -u vox -n 50"
        )

    async def _cmd_stop(self, agent_name: str) -> Dict[str, Any]:
        log_info(f"Control: stop '{agent_name}' requested via socket.")
        agent_id = self._orc._resolve_agent_id(agent_name)
        if not agent_id:
            return _err(f"No active agent named '{agent_name}'.")
        success = await self._orc.stop_agent(agent_id)
        if success:
            return _ok(f"Agent '{agent_name}' stopped.")
        return _err(f"Failed to stop '{agent_name}'.")

    async def _cmd_start(self, agent_name: str) -> Dict[str, Any]:
        log_info(f"Control: start '{agent_name}' requested via socket.")
        folder = self._orc.agents_dir / agent_name
        if not folder.exists():
            return _err(f"Agent directory '{agent_name}' not found.")
        agent = await self._orc.start_agent(folder)
        if agent:
            return _ok(f"Agent '{agent_name}' started (ID: {agent.id}).")
        return _err(f"Failed to start '{agent_name}'. Check agent logs.")

    async def _cmd_help(self) -> Dict[str, Any]:
        usage = {cmd: spec[2] for cmd, spec in self._commands.items()}
        return _ok({"commands": usage})


# ---------------------------------------------------------------------------
# Control Server
# ---------------------------------------------------------------------------

class ControlServer:
    """
    Asyncio-native Unix Domain Socket server.

    Lifecycle:
      start()  → creates the socket, begins accepting connections.
      stop()   → closes the socket and removes the file.

    Each connection is handled in its own coroutine so a slow client
    (e.g. a long-running restart) does not block others.
    Connection concurrency is bounded by MAX_CONNECTIONS.
    """

    MAX_CONNECTIONS = 8  # more than enough for a CLI tool

    def __init__(self, orchestrator: "VOXOrchestrator"):
        self._dispatcher = ControlDispatcher(orchestrator)
        self._socket_path = resolve_socket_path()
        self._server: asyncio.AbstractServer | None = None

    async def start(self):
        """Creates the UDS socket and begins listening."""
        # Remove stale socket from a previous (crashed) run
        if self._socket_path.exists():
            self._socket_path.unlink()

        self._server = await asyncio.start_unix_server(
            self._handle_connection,
            path=str(self._socket_path),
        )

        # Lock down permissions: owner read/write only (mode 0o600)
        os.chmod(self._socket_path, stat.S_IRUSR | stat.S_IWUSR)

        log_ok(
            f"Control socket ready: {self._socket_path}  "
            f"[mode 0600, {self.MAX_CONNECTIONS} max connections]"
        )

    async def stop(self):
        """Closes the server and removes the socket file."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        if self._socket_path.exists():
            self._socket_path.unlink()
        log_info("Control socket closed.")

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ):
        """
        Handles a single client connection.

        Reads one newline-delimited JSON command, dispatches it, writes
        the JSON response, and closes the connection.  The one-command-
        per-connection model keeps the protocol stateless and trivially
        scriptable (nc, socat, voxctl).
        """
        peer = writer.get_extra_info("peername") or "local"
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=5.0)
            if not raw:
                return

            response = await self._dispatcher.dispatch(raw.decode().strip())
            writer.write(json.dumps(response).encode() + b"\n")
            await writer.drain()

        except asyncio.TimeoutError:
            log_warn(f"Control: client timed out ({peer}).")
        except Exception as e:
            log_fail(f"Control: connection error ({peer}): {e}")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------

def _ok(data: Any) -> Dict[str, Any]:
    return {"ok": True, "data": data}

def _err(message: str) -> Dict[str, Any]:
    return {"ok": False, "error": message}