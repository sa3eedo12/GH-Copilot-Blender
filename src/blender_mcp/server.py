"""
MCP server for GH-Copilot-Blender.

Implements the Model Context Protocol (MCP) over stdio so that GitHub Copilot
(or any other MCP-capable client such as VS Code Agent Mode) can talk to Blender
through the Blender addon socket server.

Usage
-----
Run directly (during development)::

    python -m blender_mcp.server

Or via the installed entry-point::

    blender-mcp

The server connects to the Blender addon running on ``localhost:9876`` (configurable
via the ``BLENDER_HOST`` / ``BLENDER_PORT`` environment variables).
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import logging
import os
import socket
import sys
from typing import Any

try:
    _VERSION = importlib.metadata.version("blender-mcp")
except importlib.metadata.PackageNotFoundError:
    _VERSION = "0.0.0"

import mcp.server.stdio
import mcp.types as types
from mcp.server import Server
from mcp.server.models import InitializationOptions

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BLENDER_HOST: str = os.environ.get("BLENDER_HOST", "localhost")
BLENDER_PORT: int = int(os.environ.get("BLENDER_PORT", "9876"))

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("blender_mcp")

# ---------------------------------------------------------------------------
# Low-level Blender socket communication
# ---------------------------------------------------------------------------


def _send_command(command: dict[str, Any]) -> dict[str, Any]:
    """Send *command* to the Blender addon and return the JSON response.

    Raises ``ConnectionRefusedError`` if the addon server is not running.
    """
    payload = json.dumps(command).encode("utf-8")

    with socket.create_connection((BLENDER_HOST, BLENDER_PORT), timeout=30) as sock:
        sock.sendall(payload)

        # Read until the connection is closed by the addon
        chunks: list[bytes] = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)

    raw = b"".join(chunks).decode("utf-8")
    return json.loads(raw)


# ---------------------------------------------------------------------------
# MCP server definition
# ---------------------------------------------------------------------------

app: Server = Server("blender-mcp")


# ------------------------------------------------------------------
# Tool: get_scene_info
# ------------------------------------------------------------------

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="get_scene_info",
            description=(
                "Return a summary of the active Blender scene: object list, "
                "material count, timeline range, etc."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        types.Tool(
            name="get_object_info",
            description=(
                "Return detailed information about a specific Blender object: "
                "transform, mesh stats, materials, and world bounding box."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Exact name of the Blender object.",
                    }
                },
                "required": ["name"],
            },
        ),
        types.Tool(
            name="execute_blender_code",
            description=(
                "Execute arbitrary Python code inside Blender and return any "
                "printed output.  Use this to create objects, modify materials, "
                "animate, render, or perform any other Blender operation.\n\n"
                "⚠️  This tool has full access to the Blender Python API and the "
                "file system.  Use it only in a trusted environment."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Python source code to execute inside Blender.",
                    }
                },
                "required": ["code"],
            },
        ),
        types.Tool(
            name="get_viewport_screenshot",
            description=(
                "Capture a screenshot of the active 3D viewport and save it to "
                "*filepath*.  Returns the saved path and the image dimensions."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "Absolute path where the screenshot will be saved.",
                    },
                    "max_size": {
                        "type": "integer",
                        "description": "Maximum pixel size for the longest edge (default 800).",
                        "default": 800,
                    },
                },
                "required": ["filepath"],
            },
        ),
    ]


# ------------------------------------------------------------------
# Tool dispatch
# ------------------------------------------------------------------

@app.call_tool()
async def call_tool(
    name: str, arguments: dict[str, Any]
) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
    """Forward a Copilot tool call to the Blender addon and return the result."""

    # Map MCP tool names to Blender addon command types
    _tool_to_command: dict[str, str] = {
        "get_scene_info": "get_scene_info",
        "get_object_info": "get_object_info",
        "execute_blender_code": "execute_code",
        "get_viewport_screenshot": "get_viewport_screenshot",
    }

    cmd_type = _tool_to_command.get(name)
    if cmd_type is None:
        raise ValueError(f"Unknown tool: {name!r}")

    command: dict[str, Any] = {"type": cmd_type, "params": arguments}

    try:
        response = await asyncio.get_event_loop().run_in_executor(
            None, _send_command, command
        )
    except ConnectionRefusedError:
        return [
            types.TextContent(
                type="text",
                text=(
                    "❌  Could not connect to the Blender addon server "
                    f"({BLENDER_HOST}:{BLENDER_PORT}).  "
                    "Make sure Blender is open and the BlenderMCP addon server "
                    "is running (sidebar → BlenderMCP → Start MCP Server)."
                ),
            )
        ]
    except Exception as exc:
        logger.exception("Error communicating with Blender addon")
        return [
            types.TextContent(
                type="text",
                text=f"❌  Communication error: {exc}",
            )
        ]

    if response.get("status") == "error":
        return [
            types.TextContent(
                type="text",
                text=f"❌  Blender error: {response.get('message', 'unknown error')}",
            )
        ]

    result = response.get("result", {})
    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]


# ---------------------------------------------------------------------------
# Entry-point
# ---------------------------------------------------------------------------


async def _run() -> None:
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        init_options = InitializationOptions(
            server_name="blender-mcp",
            server_version=_VERSION,
            capabilities=app.get_capabilities(
                notification_options=None,
                experimental_capabilities={},
            ),
        )
        await app.run(read_stream, write_stream, init_options)


def main() -> None:
    """Entry-point called by the ``blender-mcp`` console script."""
    asyncio.run(_run())


if __name__ == "__main__":
    main()
