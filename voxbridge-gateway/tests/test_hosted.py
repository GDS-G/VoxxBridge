import asyncio
import sys
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest

from voxbridge import hosted
from voxbridge.hosted import (
    _child_environments,
    _hosted_environment,
    _next_restart_backoff,
    _probe_tunnel_health,
    _stop,
    _SupervisorSettings,
    _TunnelHealth,
    _watch_tunnel_health,
)

TUNNEL_ENV = {
    "CONTROL_PLANE_TUNNEL_ID": "tunnel_0123456789abcdef0123456789abcdef",
    "CONTROL_PLANE_API_KEY": "runtime-key-for-test",
}


def test_hosted_environment_forces_private_stateless_http() -> None:
    env = _hosted_environment(
        {
            **TUNNEL_ENV,
            "VOXBRIDGE_TRANSPORT": "stdio",
            "VOXBRIDGE_HOST": "0.0.0.0",
            "VOXBRIDGE_PORT": "9123",
            "VOXBRIDGE_ALLOW_REMOTE_BIND": "true",
            "MCP_SERVER_URL": "https://unsafe.example/mcp",
            "MCP_COMMAND": "unsafe-server",
            "OPENAI_ADMIN_KEY": "admin-key-must-not-reach-the-daemon",
            "TUNNEL_CLIENT_PROFILE": "unsafe-profile",
            "HEALTH_LISTEN_ADDR": "0.0.0.0:9999",
            "HEALTH_UNIX_SOCKET": "/tmp/unsafe-health.sock",
            "HEALTH_URL_FILE": "/tmp/unsafe-health-url",
            "PRESERVED": "yes",
        }
    )

    assert env["VOXBRIDGE_TRANSPORT"] == "streamable-http"
    assert env["VOXBRIDGE_HOST"] == "127.0.0.1"
    assert env["VOXBRIDGE_PORT"] == "9123"
    assert env["VOXBRIDGE_ALLOW_REMOTE_BIND"] == "false"
    assert env["MCP_SERVER_URL"] == "http://127.0.0.1:9123/mcp"
    assert "MCP_COMMAND" not in env
    assert "OPENAI_ADMIN_KEY" not in env
    assert "TUNNEL_CLIENT_PROFILE" not in env
    assert env["HEALTH_LISTEN_ADDR"] == "127.0.0.1:8080"
    assert "HEALTH_UNIX_SOCKET" not in env
    assert "HEALTH_URL_FILE" not in env
    assert env["MCP_STARTUP_WAIT_TIMEOUT"] == "60s"
    assert env["PRESERVED"] == "yes"


@pytest.mark.parametrize("port", ["zero", "0", "65536"])
def test_hosted_environment_rejects_invalid_port(port: str) -> None:
    with pytest.raises(RuntimeError, match="VOXBRIDGE_PORT"):
        _hosted_environment({**TUNNEL_ENV, "VOXBRIDGE_PORT": port})


def test_hosted_environment_requires_dedicated_runtime_key() -> None:
    with pytest.raises(RuntimeError, match="CONTROL_PLANE_API_KEY"):
        _hosted_environment(
            {
                "CONTROL_PLANE_TUNNEL_ID": TUNNEL_ENV["CONTROL_PLANE_TUNNEL_ID"],
                "OPENAI_API_KEY": "provider-key-must-not-be-used-for-the-tunnel",
            }
        )


@pytest.mark.parametrize(
    "tunnel_id",
    ["", "tunnel_too-short", "tunnel_0123456789ABCDEF0123456789ABCDEF"],
)
def test_hosted_environment_validates_tunnel_id(tunnel_id: str) -> None:
    with pytest.raises(RuntimeError, match="CONTROL_PLANE_TUNNEL_ID"):
        _hosted_environment(
            {
                "CONTROL_PLANE_TUNNEL_ID": tunnel_id,
                "CONTROL_PLANE_API_KEY": TUNNEL_ENV["CONTROL_PLANE_API_KEY"],
            }
        )


def test_child_environments_separate_runtime_and_provider_secrets() -> None:
    gateway_env, tunnel_env = _child_environments(
        {
            **TUNNEL_ENV,
            "ELEVENLABS_API_KEY": "provider-key-for-test",
            "STABILITY_API_KEY": "stability-provider-key-for-test",
            "GOOGLE_CLOUD_MUSIC_ENABLED": "true",
            "GOOGLE_CLOUD_MUSIC_LOCATION": "global",
            "OPENAI_API_KEY": "openai-provider-key-for-test",
        }
    )

    assert "CONTROL_PLANE_API_KEY" not in gateway_env
    assert "CONTROL_PLANE_TUNNEL_ID" not in gateway_env
    assert "MCP_SERVER_URL" not in gateway_env
    assert tunnel_env["CONTROL_PLANE_API_KEY"] == TUNNEL_ENV["CONTROL_PLANE_API_KEY"]
    assert "ELEVENLABS_API_KEY" not in tunnel_env
    assert "STABILITY_API_KEY" not in tunnel_env
    assert "GOOGLE_CLOUD_MUSIC_ENABLED" not in tunnel_env
    assert "GOOGLE_CLOUD_MUSIC_LOCATION" not in tunnel_env
    assert "OPENAI_API_KEY" not in tunnel_env


@pytest.mark.asyncio
async def test_stop_tolerates_process_exit_race() -> None:
    process = Mock()
    process.returncode = None
    process.terminate.side_effect = ProcessLookupError
    process.wait = AsyncMock()

    await _stop(process)

    process.wait.assert_not_awaited()


def test_probe_tunnel_health_requires_ready_control_plane(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 11, 8, 12, tzinfo=UTC)
    monkeypatch.setattr(hosted, "_read_health_status", lambda *args, **kwargs: 200)
    monkeypatch.setattr(
        hosted,
        "_read_health_component",
        lambda *args, **kwargs: {
            "schema_version": 1,
            "component": "control-plane",
            "status": "ok",
            "state": "polling",
            "observed_at": (now - timedelta(seconds=55)).isoformat(),
            "details": {
                "current_poll_age_seconds": 55,
                "deadline_seconds": 60,
            },
        },
    )

    assert _probe_tunnel_health(now=now) == _TunnelHealth(True, "control_plane_polling")

    monkeypatch.setattr(
        hosted,
        "_read_health_component",
        lambda *args, **kwargs: {
            "schema_version": 1,
            "component": "control-plane",
            "status": "degraded",
            "state": "backoff",
            "reason_code": "network_error",
        },
    )

    assert _probe_tunnel_health(now=now) == _TunnelHealth(
        False,
        "control_plane_backoff_network_error",
    )


def test_probe_tunnel_health_rejects_stopped_control_plane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hosted, "_read_health_status", lambda *args, **kwargs: 200)
    monkeypatch.setattr(
        hosted,
        "_read_health_component",
        lambda *args, **kwargs: {
            "schema_version": 1,
            "component": "control-plane",
            "status": "ok",
            "state": "stopped",
        },
    )

    assert _probe_tunnel_health() == _TunnelHealth(False, "control_plane_stopped")


def test_probe_tunnel_health_rejects_stale_ok_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 11, 8, 12, tzinfo=UTC)
    monkeypatch.setattr(hosted, "_read_health_status", lambda *args, **kwargs: 200)
    monkeypatch.setattr(
        hosted,
        "_read_health_component",
        lambda *args, **kwargs: {
            "schema_version": 1,
            "component": "control-plane",
            "status": "ok",
            "state": "idle",
            "observed_at": (now - timedelta(seconds=91)).isoformat(),
            "details": {"deadline_seconds": 60},
        },
    )

    assert _probe_tunnel_health(now=now) == _TunnelHealth(
        False,
        "control_plane_idle_stale_observation",
    )


def test_probe_tunnel_health_rejects_poll_past_deadline_margin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 11, 8, 12, tzinfo=UTC)
    monkeypatch.setattr(hosted, "_read_health_status", lambda *args, **kwargs: 200)
    monkeypatch.setattr(
        hosted,
        "_read_health_component",
        lambda *args, **kwargs: {
            "schema_version": 1,
            "component": "control-plane",
            "status": "ok",
            "state": "polling",
            "observed_at": (now - timedelta(seconds=1)).isoformat(),
            "details": {
                "current_poll_age_seconds": 91,
                "deadline_seconds": 60,
            },
        },
    )

    assert _probe_tunnel_health(now=now) == _TunnelHealth(
        False,
        "control_plane_polling_stale_poll",
    )


def test_probe_tunnel_health_accepts_fresh_backpressure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 11, 8, 12, tzinfo=UTC)
    monkeypatch.setattr(hosted, "_read_health_status", lambda *args, **kwargs: 200)
    monkeypatch.setattr(
        hosted,
        "_read_health_component",
        lambda *args, **kwargs: {
            "schema_version": 1,
            "component": "control-plane",
            "status": "ok",
            "state": "backpressured",
            "observed_at": (now - timedelta(seconds=1)).isoformat(),
            "details": {
                "current_poll_age_seconds": 0,
                "deadline_seconds": 60,
            },
        },
    )

    assert _probe_tunnel_health(now=now) == _TunnelHealth(
        True,
        "control_plane_backpressured",
    )


def test_probe_tunnel_health_rejects_unready_status(monkeypatch: pytest.MonkeyPatch) -> None:
    component = Mock()
    monkeypatch.setattr(hosted, "_read_health_status", lambda *args, **kwargs: 503)
    monkeypatch.setattr(hosted, "_read_health_component", component)

    assert _probe_tunnel_health() == _TunnelHealth(False, "readyz_http_503")
    component.assert_not_called()


@pytest.mark.asyncio
async def test_watch_tunnel_health_triggers_after_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        hosted,
        "_probe_tunnel_health",
        lambda **kwargs: _TunnelHealth(False, "control_plane_backoff_timeout"),
    )
    settings = _SupervisorSettings(
        probe_interval_seconds=0,
        unready_grace_seconds=0,
    )

    assert await _watch_tunnel_health(settings) == "control_plane_backoff_timeout"


def test_restart_backoff_is_bounded() -> None:
    settings = _SupervisorSettings(
        restart_backoff_initial_seconds=1,
        restart_backoff_max_seconds=4,
    )

    assert _next_restart_backoff(1, settings) == 2
    assert _next_restart_backoff(2, settings) == 4
    assert _next_restart_backoff(4, settings) == 4


class _FakeProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.terminate_calls = 0
        self.kill_calls = 0
        self._finished = asyncio.Event()

    async def wait(self) -> int:
        await self._finished.wait()
        assert self.returncode is not None
        return self.returncode

    def finish(self, returncode: int) -> None:
        self.returncode = returncode
        self._finished.set()

    def terminate(self) -> None:
        self.terminate_calls += 1
        self.finish(-15)

    def kill(self) -> None:
        self.kill_calls += 1
        self.finish(-9)


@pytest.mark.asyncio
async def test_run_restarts_unready_tunnel_without_restarting_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = _FakeProcess()
    tunnels: list[_FakeProcess] = []

    async def spawn(*command: str, **kwargs: object) -> _FakeProcess:
        if command[:3] == (sys.executable, "-m", "voxbridge.server"):
            return gateway
        tunnel = _FakeProcess()
        tunnels.append(tunnel)
        if len(tunnels) == 2:
            gateway.finish(7)
        return tunnel

    watch_calls = 0

    async def watch(settings: _SupervisorSettings) -> str:
        nonlocal watch_calls
        watch_calls += 1
        if watch_calls == 1:
            return "control_plane_backoff_network_error"
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    monkeypatch.setattr(hosted, "_child_environments", lambda: ({}, {}))
    monkeypatch.setattr(hosted.shutil, "which", lambda name: "tunnel-client")
    monkeypatch.setattr(hosted.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(hosted, "_watch_tunnel_health", watch)

    result = await hosted.run(
        _SupervisorSettings(
            restart_backoff_initial_seconds=0,
            restart_backoff_max_seconds=0,
        )
    )

    assert result == 7
    assert len(tunnels) == 2
    assert gateway.terminate_calls == 0
    assert tunnels[0].terminate_calls == 1
    assert tunnels[1].terminate_calls == 1
