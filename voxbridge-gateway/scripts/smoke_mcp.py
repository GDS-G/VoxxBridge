"""Smoke-test a running VoxBridge Streamable HTTP endpoint."""

from __future__ import annotations

import argparse
import asyncio

from mcp.client import Client


async def check(url: str) -> None:
    async with Client(url, mode="auto") as client:
        tools = await client.list_tools(cache_mode="reload")
        names = {tool.name for tool in tools.tools}
        expected = {"list_providers", "list_voices", "generate_speech"}
        if names != expected:
            raise RuntimeError(f"Unexpected MCP tools: {sorted(names)}")
        generate = next(tool for tool in tools.tools if tool.name == "generate_speech")
        delivery = generate.input_schema.get("properties", {}).get("delivery", {})
        if delivery.get("default") != "both" or set(delivery.get("enum", [])) != {
            "playback",
            "file",
            "both",
        }:
            raise RuntimeError("generate_speech delivery schema is unavailable")
        templates = await client.list_resource_templates(cache_mode="reload")
        template_uris = {str(item.uri_template) for item in templates.resource_templates}
        if not {
            "voxbridge://audio/mp3/{token}/{file_name}",
            "voxbridge://audio/wav/{token}/{file_name}",
        }.issubset(template_uris):
            raise RuntimeError("VoxBridge audio download resource templates are unavailable")
        result = await client.call_tool("list_providers", {})
        if result.is_error:
            raise RuntimeError("list_providers returned an MCP tool error")
        print(f"VoxBridge MCP smoke test passed: {url} ({len(names)} tools)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("url", nargs="?", default="http://127.0.0.1:8000/mcp")
    args = parser.parse_args()
    asyncio.run(check(args.url))


if __name__ == "__main__":
    main()
