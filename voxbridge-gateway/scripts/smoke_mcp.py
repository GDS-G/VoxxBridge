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
        delivery_output_schema = generate.output_schema or {}
        if not {
            "delivery",
            "file_size_bytes",
            "sha256",
            "materialize_max_bytes",
        }.issubset(delivery_output_schema.get("properties", {})):
            raise RuntimeError("generate_speech output schema is unavailable")
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
        if dialogue.output_schema != generate.output_schema:
            raise RuntimeError("generate_dialogue output schema is unavailable")
        materialize = next(tool for tool in tools.tools if tool.name == "materialize_audio_file")
        materialize_properties = materialize.input_schema.get("properties", {})
        if materialize.input_schema.get("required") or not {"resource_uri", "file_name"}.issubset(
            materialize_properties
        ):
            raise RuntimeError("materialize_audio_file schema is unavailable")
        materialize_output_schema = materialize.output_schema or {}
        if not {
            "synthetic_audio",
            "materialized",
            "file_name",
            "file_mime_type",
            "file_size_bytes",
            "sha256",
            "source_resource_uri",
        }.issubset(materialize_output_schema.get("properties", {})):
            raise RuntimeError("materialize_audio_file output schema is unavailable")
        materialize_meta = materialize.meta or {}
        if materialize_meta.get("ui", {}).get("visibility") != ["model", "app"]:
            raise RuntimeError("materialize_audio_file is not visible to the MCP App")
        if materialize_meta.get("openai/widgetAccessible") is not True:
            raise RuntimeError("materialize_audio_file is not accessible to the ChatGPT widget")
        ui_uri = "ui://voxbridge/audio-delivery-v11.html"
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
            or "materialize_audio_file" not in ui_document.contents[0].text
            or "callServerTool" not in ui_document.contents[0].text
            or "Add file to ChatGPT" not in ui_document.contents[0].text
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
