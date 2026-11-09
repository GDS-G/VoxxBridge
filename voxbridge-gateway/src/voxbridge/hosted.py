from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import shutil
import signal
import sys
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

LOGGER = logging.getLogger("voxbridge.hosted")
TUNNEL_ID_PATTERN = re.compile(r"tunnel_[0-9a-f]{32}")
SAFE_HEALTH_VALUE_PATTERN = re.compile(r"[a-z0-9_-]{1,64}")
TUNNEL_HEALTH_BASE_URL = "http://127.0.0.1:8080"
TUNNEL_HEALTH_MAX_RESPONSE_BYTES = 64 * 1024
TUNNEL_HEALTH_STALE_MARGIN_SECONDS = 30.0
TUNNEL_HEALTH_ACTIVE_STATES = frozenset({"polling", "idle", "backpressured"})
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


@dataclass(frozen=True)
class _TunnelHealth:
    ready: bool
    reason: str


@dataclass(frozen=True)
class _SupervisorSettings:
    probe_interval_seconds: float = 15.0
    probe_timeout_seconds: float = 2.0
    unready_grace_seconds: float = 90.0
    restart_backoff_initial_seconds: float = 1.0
    restart_backoff_max_seconds: float = 30.0
    restart_backoff_reset_seconds: float = 300.0


def _read_health_status(path: str, *, timeout_seconds: float) -> int:
    request = urllib.request.Request(
        f"{TUNNEL_HEALTH_BASE_URL}{path}",
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        # Probe status is useful; the response body can contain diagnostic text
        # that does not belong in supervisor logs.
        return exc.code


def _read_health_component(path: str, *, timeout_seconds: float) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{TUNNEL_HEALTH_BASE_URL}{path}",
        headers={"Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        if response.status != 200:
            raise RuntimeError(f"health endpoint returned HTTP {response.status}")
        payload = response.read(TUNNEL_HEALTH_MAX_RESPONSE_BYTES + 1)
    if len(payload) > TUNNEL_HEALTH_MAX_RESPONSE_BYTES:
        raise ValueError("health response exceeded the size limit")
    decoded = json.loads(payload)
    if not isinstance(decoded, dict):
        raise TypeError("health response must be a JSON object")
    return decoded


def _safe_health_value(value: object, fallback: str) -> str:
    if isinstance(value, str) and SAFE_HEALTH_VALUE_PATTERN.fullmatch(value):
        return value
    return fallback


def _nonnegative_health_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return None
    return number


def _health_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _probe_tunnel_health(
    *,
    timeout_seconds: float = 2.0,
    now: datetime | None = None,
) -> _TunnelHealth:
    """Read passive tunnel-client health without logging response content."""
    try:
        readiness_status = _read_health_status("/readyz", timeout_seconds=timeout_seconds)
    except (OSError, TimeoutError, ValueError):
        return _TunnelHealth(False, "readyz_unreachable")
    if readiness_status != 200:
        return _TunnelHealth(False, f"readyz_http_{readiness_status}")

    try:
        control_plane = _read_health_component(
            "/health/control-plane", timeout_seconds=timeout_seconds
        )
    except (OSError, TimeoutError, RuntimeError, TypeError, ValueError):
        return _TunnelHealth(False, "control_plane_unavailable")

    if control_plane.get("schema_version") != 1:
        return _TunnelHealth(False, "control_plane_schema")
    if control_plane.get("component") != "control-plane":
        return _TunnelHealth(False, "control_plane_component")

    status = _safe_health_value(control_plane.get("status"), "invalid")
    state = _safe_health_value(control_plane.get("state"), "invalid")
    if state == "stopped":
        return _TunnelHealth(False, "control_plane_stopped")
    if status != "ok":
        reason = _safe_health_value(control_plane.get("reason_code"), status)
        return _TunnelHealth(False, f"control_plane_{state}_{reason}")
    if state not in TUNNEL_HEALTH_ACTIVE_STATES:
        return _TunnelHealth(False, "control_plane_invalid_state")

    observed_at = _health_timestamp(control_plane.get("observed_at"))
    details = control_plane.get("details")
    if observed_at is None or not isinstance(details, dict):
        return _TunnelHealth(False, f"control_plane_{state}_invalid_observation")
    deadline_seconds = _nonnegative_health_number(details.get("deadline_seconds"))
    if deadline_seconds is None or deadline_seconds == 0:
        return _TunnelHealth(False, f"control_plane_{state}_invalid_timing")

    checked_at = now or datetime.now(UTC)
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=UTC)
    else:
        checked_at = checked_at.astimezone(UTC)
    observation_age_seconds = (checked_at - observed_at).total_seconds()
    freshness_limit_seconds = deadline_seconds + TUNNEL_HEALTH_STALE_MARGIN_SECONDS
    if observation_age_seconds < -TUNNEL_HEALTH_STALE_MARGIN_SECONDS:
        return _TunnelHealth(False, f"control_plane_{state}_invalid_observation")
    if observation_age_seconds > freshness_limit_seconds:
        return _TunnelHealth(False, f"control_plane_{state}_stale_observation")

    if state == "polling":
        poll_age_seconds = _nonnegative_health_number(details.get("current_poll_age_seconds"))
        if poll_age_seconds is None:
            return _TunnelHealth(False, "control_plane_polling_invalid_timing")
        if poll_age_seconds > freshness_limit_seconds:
            return _TunnelHealth(False, "control_plane_polling_stale_poll")

    return _TunnelHealth(True, f"control_plane_{state}")


async def _watch_tunnel_health(settings: _SupervisorSettings) -> str:
    """Return after tunnel readiness remains unhealthy for the grace period."""
    loop = asyncio.get_running_loop()
    unhealthy_since: float | None = None
    last_reason: str | None = None

    while True:
        health = await asyncio.to_thread(
            _probe_tunnel_health,
            timeout_seconds=settings.probe_timeout_seconds,
        )
        now = loop.time()
        if health.ready:
            if unhealthy_since is not None:
                LOGGER.info("tunnel-client health recovered")
            unhealthy_since = None
            last_reason = None
        else:
            if unhealthy_since is None:
                unhealthy_since = now
                LOGGER.warning("tunnel-client is not ready: %s", health.reason)
            elif health.reason != last_reason:
                LOGGER.warning("tunnel-client remains unready: %s", health.reason)
            last_reason = health.reason
            if now - unhealthy_since >= settings.unready_grace_seconds:
                return health.reason
        await asyncio.sleep(settings.probe_interval_seconds)


def _next_restart_backoff(current: float, settings: _SupervisorSettings) -> float:
    return min(
        settings.restart_backoff_max_seconds,
        max(settings.restart_backoff_initial_seconds, current * 2),
    )


async def _wait_for_restart_delay(
    delay_seconds: float,
    gateway_wait: asyncio.Task[int],
    stop_wait: asyncio.Task[bool],
) -> str:
    delay_wait = asyncio.create_task(asyncio.sleep(delay_seconds))
    try:
        done, _ = await asyncio.wait(
            (gateway_wait, stop_wait, delay_wait),
            return_when=asyncio.FIRST_COMPLETED,
        )
        if stop_wait in done:
            return "stop"
        if gateway_wait in done:
            return "gateway"
        return "elapsed"
    finally:
        if not delay_wait.done():
            delay_wait.cancel()
        await asyncio.gather(delay_wait, return_exceptions=True)


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
    env["HEALTH_LISTEN_ADDR"] = "127.0.0.1:8080"
    env.setdefault("MCP_STARTUP_WAIT_TIMEOUT", "60s")
    env.pop("HEALTH_UNIX_SOCKET", None)
    env.pop("HEALTH_URL_FILE", None)
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


async def run(settings: _SupervisorSettings | None = None) -> int:
    settings = settings or _SupervisorSettings()
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
    gateway_wait = asyncio.create_task(gateway.wait())
    stop_requested = asyncio.Event()
    stop_wait = asyncio.create_task(stop_requested.wait())
    cycle_waiters: list[asyncio.Task[int] | asyncio.Task[str]] = []
    loop = asyncio.get_running_loop()
    registered_signals: list[signal.Signals] = []

    for handled_signal in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(handled_signal, stop_requested.set)
            registered_signals.append(handled_signal)
        except NotImplementedError:  # pragma: no cover - Linux container supports this
            pass

    restart_backoff = settings.restart_backoff_initial_seconds
    try:
        while True:
            tunnel = await asyncio.create_subprocess_exec(tunnel_binary, "run", env=tunnel_env)
            tunnel_started_at = loop.time()
            tunnel_wait = asyncio.create_task(tunnel.wait())
            health_wait = asyncio.create_task(_watch_tunnel_health(settings))
            cycle_waiters = [tunnel_wait, health_wait]
            done, _ = await asyncio.wait(
                (gateway_wait, stop_wait, tunnel_wait, health_wait),
                return_when=asyncio.FIRST_COMPLETED,
            )

            if stop_wait in done:
                return 0
            if gateway_wait in done:
                return_code = gateway_wait.result()
                LOGGER.error("gateway exited unexpectedly with status %s", return_code)
                return return_code or 1

            if tunnel_wait in done:
                return_code = tunnel_wait.result()
                LOGGER.error(
                    "tunnel-client exited unexpectedly with status %s; restarting",
                    return_code,
                )
            else:
                reason = health_wait.result()
                LOGGER.error(
                    "tunnel-client remained unready beyond the grace period (%s); restarting",
                    reason,
                )
                await _stop(tunnel)

            for waiter in cycle_waiters:
                if not waiter.done():
                    waiter.cancel()
            await asyncio.gather(*cycle_waiters, return_exceptions=True)
            cycle_waiters = []

            if loop.time() - tunnel_started_at >= settings.restart_backoff_reset_seconds:
                restart_backoff = settings.restart_backoff_initial_seconds
            LOGGER.info("restarting tunnel-client in %.1f seconds", restart_backoff)
            delay_result = await _wait_for_restart_delay(
                restart_backoff,
                gateway_wait,
                stop_wait,
            )
            restart_backoff = _next_restart_backoff(restart_backoff, settings)
            if delay_result == "stop":
                return 0
            if delay_result == "gateway":
                return_code = gateway_wait.result()
                LOGGER.error("gateway exited unexpectedly with status %s", return_code)
                return return_code or 1
    finally:
        # Stop ingress before the gateway so no new tunneled request arrives while
        # the local MCP process is shutting down.
        await _stop(tunnel)
        await _stop(gateway)
        waiters: list[asyncio.Task[int] | asyncio.Task[bool] | asyncio.Task[str]] = [
            gateway_wait,
            stop_wait,
            *cycle_waiters,
        ]
        for waiter in waiters:
            if not waiter.done():
                waiter.cancel()
        await asyncio.gather(*waiters, return_exceptions=True)
        for handled_signal in registered_signals:
            loop.remove_signal_handler(handled_signal)


def main() -> None:
    logging.basicConfig(level=os.environ.get("VOXBRIDGE_LOG_LEVEL", "INFO"))
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
