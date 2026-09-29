from unittest.mock import AsyncMock, Mock

import pytest

from voxbridge.hosted import _child_environments, _hosted_environment, _stop

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
            "OPENAI_API_KEY": "openai-provider-key-for-test",
        }
    )

    assert "CONTROL_PLANE_API_KEY" not in gateway_env
    assert "CONTROL_PLANE_TUNNEL_ID" not in gateway_env
    assert "MCP_SERVER_URL" not in gateway_env
    assert tunnel_env["CONTROL_PLANE_API_KEY"] == TUNNEL_ENV["CONTROL_PLANE_API_KEY"]
    assert "ELEVENLABS_API_KEY" not in tunnel_env
    assert "OPENAI_API_KEY" not in tunnel_env


@pytest.mark.asyncio
async def test_stop_tolerates_process_exit_race() -> None:
    process = Mock()
    process.returncode = None
    process.terminate.side_effect = ProcessLookupError
    process.wait = AsyncMock()

    await _stop(process)

    process.wait.assert_not_awaited()
