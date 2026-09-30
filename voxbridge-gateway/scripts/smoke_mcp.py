"""Smoke-test a running VoxBridge Streamable HTTP endpoint."""

from __future__ import annotations

import argparse
import asyncio

from mcp.client import Client


async def check(url: str) -> None:
    async with Client(url, mode="auto") as client:
        tools = await client.list_tools(cache_mode="reload")
        names = {tool.name for tool in tools.tools}
        expected = {
            "list_providers",
            "list_voices",
            "generate_speech",
            "generate_dialogue",
            "materialize_audio_file",
        }
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
        dialogue = next(tool for tool in tools.tools if tool.name == "generate_dialogue")
        dialogue_delivery = dialogue.input_schema.get("properties", {}).get("delivery", {})
        if dialogue_delivery.get("default") != "both" or set(dialogue_delivery.get("enum", [])) != {
            "playback",
            "file",
            "both",
        }:
            raise RuntimeError("generate_dialogue delivery schema is unavailable")
        if dialogue.meta != generate.meta:
            raise RuntimeError("generate_dialogue MCP App binding is unavailable")
        materialize = next(tool for tool in tools.tools if tool.name == "materialize_audio_file")
        if set(materialize.input_schema.get("required", [])) != {"resource_uri"}:
            raise RuntimeError("materialize_audio_file schema is unavailable")
        ui_uri = "ui://voxbridge/audio-delivery-v6.html"
        if generate.meta is None or generate.meta.get("ui", {}).get("resourceUri") != ui_uri:
            raise RuntimeError("generate_speech MCP App binding is unavailable")
        resources = await client.list_resources(cache_mode="reload")
        ui_resources = {str(item.uri): item for item in resources.resources}
        if ui_uri not in ui_resources:
            raise RuntimeError("VoxBridge audio delivery app resource is unavailable")
        if ui_resources[ui_uri].mime_type != "text/html;profile=mcp-app":
            raise RuntimeError("VoxBridge audio delivery app has an invalid media type")
        ui_document = await client.read_resource(ui_uri, cache_mode="bypass")
        if (
            not ui_document.contents
            or "downloadFile" not in ui_document.contents[0].text
            or "uploadFile" not in ui_document.contents[0].text
            or "getFileDownloadUrl" not in ui_document.contents[0].text
        ):
            raise RuntimeError("VoxBridge audio delivery app is invalid")
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
