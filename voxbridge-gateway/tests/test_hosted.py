import pytest

from voxbridge.hosted import _hosted_environment


def test_hosted_environment_forces_private_stateless_http() -> None:
    env = _hosted_environment(
        {
            "VOXBRIDGE_TRANSPORT": "stdio",
            "VOXBRIDGE_HOST": "0.0.0.0",
            "VOXBRIDGE_PORT": "9123",
            "VOXBRIDGE_ALLOW_REMOTE_BIND": "true",
            "MCP_SERVER_URL": "https://unsafe.example/mcp",
            "PRESERVED": "yes",
        }
    )

    assert env["VOXBRIDGE_TRANSPORT"] == "streamable-http"
    assert env["VOXBRIDGE_HOST"] == "127.0.0.1"
    assert env["VOXBRIDGE_PORT"] == "9123"
    assert env["VOXBRIDGE_ALLOW_REMOTE_BIND"] == "false"
    assert env["MCP_SERVER_URL"] == "http://127.0.0.1:9123/mcp"
    assert env["MCP_STARTUP_WAIT_TIMEOUT"] == "60s"
    assert env["PRESERVED"] == "yes"


@pytest.mark.parametrize("port", ["zero", "0", "65536"])
def test_hosted_environment_rejects_invalid_port(port: str) -> None:
    with pytest.raises(RuntimeError, match="VOXBRIDGE_PORT"):
        _hosted_environment({"VOXBRIDGE_PORT": port})
