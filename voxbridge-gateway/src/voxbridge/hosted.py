from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import signal
import sys
from collections.abc import Mapping

LOGGER = logging.getLogger("voxbridge.hosted")
TUNNEL_ID_PATTERN = re.compile(r"tunnel_[0-9a-f]{32}")
PROVIDER_ENVIRONMENT_KEYS = {
    "AZURE_SPEECH_KEY",
    "AZURE_SPEECH_REGION",
    "CARTESIA_API_KEY",
    "CARTESIA_VERSION",
    "DEEPGRAM_API_KEY",
    "ELEVENLABS_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_CLOUD_MUSIC_ENABLED",
    "GOOGLE_CLOUD_MUSIC_LOCATION",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_TTS_ENABLED",
    "HUME_API_KEY",
    "OPENAI_API_KEY",
    "RESEMBLE_API_KEY",
    "STABILITY_API_KEY",
}


def _hosted_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Build the shared environment for the private hosted topology.

    The gateway is always kept on container loopback. The tunnel client reaches
    it over stateless Streamable HTTP, avoiding the process-affinity hazard of
    multiple stdio children. Overlapping revisions must remain protocol-compatible.
    """
    env = dict(os.environ if source is None else source)
    tunnel_id = env.get("CONTROL_PLANE_TUNNEL_ID", "").strip()
    if not TUNNEL_ID_PATTERN.fullmatch(tunnel_id):
        raise RuntimeError(
            "CONTROL_PLANE_TUNNEL_ID must be 'tunnel_' followed by 32 lowercase hex characters"
        )
    if not env.get("CONTROL_PLANE_API_KEY", "").strip():
        raise RuntimeError(
            "CONTROL_PLANE_API_KEY is required; the hosted gateway will not fall back "
            "to the provider OPENAI_API_KEY"
        )

    raw_port = env.get("VOXBRIDGE_PORT", "8000")
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise RuntimeError("VOXBRIDGE_PORT must be an integer") from exc
    if not 1 <= port <= 65_535:
        raise RuntimeError("VOXBRIDGE_PORT must be between 1 and 65535")

    env["VOXBRIDGE_TRANSPORT"] = "streamable-http"
    env["VOXBRIDGE_HOST"] = "127.0.0.1"
    env["VOXBRIDGE_PORT"] = str(port)
    env["VOXBRIDGE_ALLOW_REMOTE_BIND"] = "false"
    env["CONTROL_PLANE_TUNNEL_ID"] = tunnel_id
    env["MCP_SERVER_URL"] = f"http://127.0.0.1:{port}/mcp"
    env.setdefault("MCP_STARTUP_WAIT_TIMEOUT", "60s")
    env.pop("MCP_COMMAND", None)
    env.pop("OPENAI_ADMIN_KEY", None)
    env.pop("TUNNEL_CLIENT_CONFIG", None)
    env.pop("TUNNEL_CLIENT_PROFILE", None)
    env.pop("TUNNEL_CLIENT_PROFILE_FILE", None)
    return env


def _child_environments(
    source: Mapping[str, str] | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    shared = _hosted_environment(source)
    gateway_env = shared.copy()
    gateway_env.pop("CONTROL_PLANE_API_KEY", None)
    gateway_env.pop("CONTROL_PLANE_TUNNEL_ID", None)
    gateway_env.pop("MCP_SERVER_URL", None)
    gateway_env.pop("MCP_STARTUP_WAIT_TIMEOUT", None)

    tunnel_env = shared.copy()
    for key in PROVIDER_ENVIRONMENT_KEYS:
        tunnel_env.pop(key, None)
    return gateway_env, tunnel_env


async def _stop(process: asyncio.subprocess.Process | None) -> None:
    if process is None or process.returncode is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=10)
    except TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await process.wait()


async def run() -> int:
    gateway_env, tunnel_env = _child_environments()
    tunnel_binary = shutil.which("tunnel-client")
    if tunnel_binary is None:
        raise RuntimeError("tunnel-client is not installed")

    gateway = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "voxbridge.server",
        env=gateway_env,
    )
    tunnel: asyncio.subprocess.Process | None = None
    waiters: list[asyncio.Task[int] | asyncio.Task[bool]] = []
    stop_requested = asyncio.Event()
    loop = asyncio.get_running_loop()

    for handled_signal in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(handled_signal, stop_requested.set)
        except NotImplementedError:  # pragma: no cover - Linux container supports this
            pass

    try:
        tunnel = await asyncio.create_subprocess_exec(tunnel_binary, "run", env=tunnel_env)
        gateway_wait = asyncio.create_task(gateway.wait())
        tunnel_wait = asyncio.create_task(tunnel.wait())
        stop_wait = asyncio.create_task(stop_requested.wait())
        waiters = [gateway_wait, tunnel_wait, stop_wait]
        done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)

        if stop_wait in done:
            return 0

        failed_wait = gateway_wait if gateway_wait in done else tunnel_wait
        component = "gateway" if failed_wait is gateway_wait else "tunnel-client"
        return_code = failed_wait.result()
        LOGGER.error("%s exited unexpectedly with status %s", component, return_code)
        return return_code or 1
    finally:
        # Stop ingress before the gateway so no new tunneled request arrives while
        # the local MCP process is shutting down.
        await _stop(tunnel)
        await _stop(gateway)
        for waiter in waiters:
            if not waiter.done():
                waiter.cancel()
        if waiters:
            await asyncio.gather(*waiters, return_exceptions=True)


def main() -> None:
    logging.basicConfig(level=os.environ.get("VOXBRIDGE_LOG_LEVEL", "INFO"))
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
