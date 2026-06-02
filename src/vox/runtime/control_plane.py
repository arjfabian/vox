"""Unix Domain Socket control plane for VOX."""

import asyncio
import json
import os

from vox.runtime import VOXRuntime
from vox.observability import VOXForensicLogger


async def _handle_status(orchestrator, args):
    return {"data": orchestrator.get_fleet_snapshot(), "ok": True}


async def _handle_list(orchestrator, args):
    snapshot = orchestrator.get_fleet_snapshot()
    return {"data": {"agents": snapshot["agents"]}, "ok": True}


async def _handle_stop(orchestrator, args):
    if not args:
        return {"error": "Agent name required."}
    agent_name = args[0]
    agent_id = orchestrator.resolve_agent_id(agent_name)
    success = await orchestrator.stop_agent(agent_id) if agent_id else False
    if success:
        return {"data": f"Agent '{agent_name}' stopped successfully.", "ok": True}
    return {"error": f"Command 'stop' failed for '{agent_name}'."}


async def _handle_restart(orchestrator, args):
    if not args:
        return {"error": "Agent name required."}
    agent_name = args[0]
    success = await orchestrator.restart_agent(agent_name)
    if success:
        return {"data": f"Agent '{agent_name}' restarted successfully.", "ok": True}
    return {"error": f"Command 'restart' failed for '{agent_name}'."}


async def _handle_start(orchestrator, args):
    if not args:
        return {"error": "Agent name required."}
    agent_name = args[0]
    success = await orchestrator.start_agent_by_name(agent_name)
    if success:
        return {"data": f"Agent '{agent_name}' started successfully.", "ok": True}
    return {"error": f"Command 'start' failed for '{agent_name}'."}


_COMMAND_HANDLERS = {
    "status": _handle_status,
    "list": _handle_list,
    "stop": _handle_stop,
    "restart": _handle_restart,
    "start": _handle_start,
}


async def handle_control_command(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    runtime: VOXRuntime,
    logger: VOXForensicLogger,
) -> None:
    try:
        data = await reader.readline()
        if not data:
            return
        request = json.loads(data.decode().strip())
        cmd = request.get("cmd")
        args = request.get("args", [])
        logger.info(f"UDS Command: {cmd}({', '.join(args)})")

        handler = _COMMAND_HANDLERS.get(cmd)
        if handler:
            response = await handler(runtime.orchestrator, args)
        else:
            response = {"error": f"Unknown command: '{cmd}'."}

        writer.write((json.dumps(response) + "\n").encode())
        await writer.drain()

    except Exception as e:
        error_msg = f"UDS Server Error: {e}"
        logger.error(error_msg)
        try:
            writer.write(
                (json.dumps({"ok": False, "error": error_msg}) + "\n").encode()
            )
            await writer.drain()
        except Exception:
            logger.warning("Failed to send error response to UDS client")
    finally:
        writer.close()
        await writer.wait_closed()


async def start_control_plane(
    runtime: VOXRuntime,
    logger: VOXForensicLogger,
) -> None:
    if os.path.exists(runtime.config.uds_path):
        os.remove(runtime.config.uds_path)

    server = await asyncio.start_unix_server(
        lambda r, w: handle_control_command(r, w, runtime, logger),
        path=str(runtime.config.uds_path),
    )
    os.chmod(runtime.config.uds_path, 0o600)
    logger.info(f"Control plane active at {runtime.config.uds_path} (mode 0600)")

    try:
        async with server:
            await server.serve_forever()
    finally:
        if os.path.exists(runtime.config.uds_path):
            os.remove(runtime.config.uds_path)
