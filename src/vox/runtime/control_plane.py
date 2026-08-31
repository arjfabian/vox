"""Unix Domain Socket control plane for VOX."""

import asyncio
import json
import os

from vox.observability import VOXForensicLogger
from vox.runtime import VOXRuntime


async def _handle_status(orchestrator, args):
    return {"data": orchestrator.get_fleet_snapshot(), "ok": True}


async def _handle_list(orchestrator, args):
    snapshot = orchestrator.get_fleet_snapshot()
    return {"data": {"workloads": snapshot["workloads"]}, "ok": True}


async def _handle_stop(orchestrator, args):
    if not args:
        return {"error": "Workload name required."}
    workload_name = args[0]
    workload_id = orchestrator.resolve_workload_id(workload_name)
    success = await orchestrator.stop_workload(workload_id) if workload_id else False
    if success:
        return {
            "data": f"Workload '{workload_name}' stopped successfully.",
            "ok": True,
        }
    return {"error": f"Command 'stop' failed for '{workload_name}'."}


async def _handle_restart(orchestrator, args):
    if not args:
        return {"error": "Workload name required."}
    workload_name = args[0]
    success = await orchestrator.restart_workload(workload_name)
    if success:
        return {
            "data": f"Workload '{workload_name}' restarted successfully.",
            "ok": True,
        }
    return {"error": f"Command 'restart' failed for '{workload_name}'."}


async def _handle_start(orchestrator, args):
    if not args:
        return {"error": "Workload name required."}
    workload_name = args[0]
    success = await orchestrator.start_workload_by_name(workload_name)
    if success:
        return {
            "data": f"Workload '{workload_name}' started successfully.",
            "ok": True,
        }
    return {"error": f"Command 'start' failed for '{workload_name}'."}


async def _handle_pause(orchestrator, args):
    if not args:
        return {"error": "Workload name required."}
    workload_name = args[0]
    success = await orchestrator.pause_workload(workload_name)
    if success:
        return {"data": f"Workload '{workload_name}' paused.", "ok": True}
    return {"error": f"Command 'pause' failed for '{workload_name}'."}


async def _handle_resume(orchestrator, args):
    if not args:
        return {"error": "Workload name required."}
    workload_name = args[0]
    success = await orchestrator.resume_workload(workload_name)
    if success:
        return {"data": f"Workload '{workload_name}' resumed.", "ok": True}
    return {"error": f"Command 'resume' failed for '{workload_name}'."}


_COMMAND_HANDLERS = {
    "status": _handle_status,
    "list": _handle_list,
    "stop": _handle_stop,
    "restart": _handle_restart,
    "start": _handle_start,
    "pause": _handle_pause,
    "resume": _handle_resume,
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

    except Exception as e:  # noqa: BLE001 — UDS handler must not crash
        error_msg = f"UDS Server Error: {e}"
        logger.error(error_msg)
        try:
            writer.write(
                (json.dumps({"ok": False, "error": error_msg}) + "\n").encode()
            )
            await writer.drain()
        except Exception:  # noqa: BLE001 — best-effort error response
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
