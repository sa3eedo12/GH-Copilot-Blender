"""
GH-Copilot-Blender: Blender addon that exposes a socket server for the Blender MCP.

Install this file as a Blender addon:
  Edit > Preferences > Add-ons > Install… > select addon.py > enable it.

After enabling, open the 3D Viewport sidebar (N), find the "BlenderMCP" tab,
and click "Start MCP Server".
"""

import io
import json
import socket
import ssl
import textwrap
import threading
import time
import traceback
import urllib.error
import urllib.request

import bpy
import mathutils
from bpy.props import BoolProperty, IntProperty, StringProperty

# ---------------------------------------------------------------------------
# Addon metadata
# ---------------------------------------------------------------------------

bl_info = {
    "name": "Blender MCP",
    "author": "GH-Copilot-Blender",
    "version": (1, 0, 0),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar > BlenderMCP",
    "description": "Connect Blender to GitHub Copilot via the Model Context Protocol (MCP)",
    "category": "Interface",
}

# ---------------------------------------------------------------------------
# Socket / MCP server
# ---------------------------------------------------------------------------

class BlenderMCPServer:
    """TCP socket server that runs inside Blender and executes MCP commands."""

    def __init__(self, host: str = "localhost", port: int = 9876):
        self.host = host
        self.port = port
        self.running = False
        self.socket: socket.socket | None = None
        self.server_thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self.running:
            print("[BlenderMCP] Server is already running")
            return

        self.running = True
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind((self.host, self.port))
            self.socket.listen(5)

            self.server_thread = threading.Thread(target=self._server_loop, daemon=True)
            self.server_thread.start()

            print(f"[BlenderMCP] Server started on {self.host}:{self.port}")
        except Exception as exc:
            print(f"[BlenderMCP] Failed to start server: {exc}")
            self.running = False
            self._close_socket()

    def stop(self) -> None:
        self.running = False
        self._close_socket()

        if self.server_thread and self.server_thread.is_alive():
            self.server_thread.join(timeout=2.0)
        self.server_thread = None

        print("[BlenderMCP] Server stopped")

    def _close_socket(self) -> None:
        if self.socket:
            try:
                self.socket.close()
            except Exception:
                pass
            self.socket = None

    # ------------------------------------------------------------------
    # Networking
    # ------------------------------------------------------------------

    def _server_loop(self) -> None:
        """Accept connections in the background thread."""
        self.socket.settimeout(1.0)
        print("[BlenderMCP] Server thread started")

        while self.running:
            try:
                try:
                    client, address = self.socket.accept()
                    print(f"[BlenderMCP] Client connected: {address}")
                    t = threading.Thread(
                        target=self._handle_client, args=(client,), daemon=True
                    )
                    t.start()
                except socket.timeout:
                    continue
                except OSError:
                    # Socket was closed – normal on stop()
                    break
            except Exception as exc:
                if self.running:
                    print(f"[BlenderMCP] Error in server loop: {exc}")
                    time.sleep(0.5)

        print("[BlenderMCP] Server thread stopped")

    def _handle_client(self, client: socket.socket) -> None:
        """Read JSON commands from the client and schedule execution in the main thread."""
        client.settimeout(None)
        buffer = b""

        try:
            while self.running:
                try:
                    chunk = client.recv(8192)
                    if not chunk:
                        print("[BlenderMCP] Client disconnected")
                        break
                    buffer += chunk

                    # Support newline-delimited JSON as well as a single JSON blob
                    while buffer:
                        try:
                            command = json.loads(buffer.decode("utf-8"))
                            buffer = b""
                        except json.JSONDecodeError:
                            break

                        def _run(cmd=command, conn=client):
                            try:
                                response = self.execute_command(cmd)
                            except Exception as exc:
                                response = {"status": "error", "message": str(exc)}
                            try:
                                conn.sendall(json.dumps(response).encode("utf-8"))
                            except Exception:
                                pass
                            return None  # Unregister the timer

                        bpy.app.timers.register(_run, first_interval=0.0)
                except Exception as exc:
                    print(f"[BlenderMCP] Error receiving data: {exc}")
                    break
        except Exception as exc:
            print(f"[BlenderMCP] Error in client handler: {exc}")
        finally:
            try:
                client.close()
            except Exception:
                pass
            print("[BlenderMCP] Client handler stopped")

    # ------------------------------------------------------------------
    # Command dispatch
    # ------------------------------------------------------------------

    def execute_command(self, command: dict) -> dict:
        try:
            return self._dispatch(command)
        except Exception as exc:
            traceback.print_exc()
            return {"status": "error", "message": str(exc)}

    def _dispatch(self, command: dict) -> dict:
        cmd_type = command.get("type")
        params = command.get("params", {})

        handlers = {
            "get_scene_info": self.get_scene_info,
            "get_object_info": self.get_object_info,
            "execute_code": self.execute_code,
            "get_viewport_screenshot": self.get_viewport_screenshot,
        }

        handler = handlers.get(cmd_type)
        if handler is None:
            return {"status": "error", "message": f"Unknown command: {cmd_type!r}"}

        try:
            result = handler(**params)
            return {"status": "success", "result": result}
        except Exception as exc:
            traceback.print_exc()
            return {"status": "error", "message": str(exc)}

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def get_scene_info(self) -> dict:
        """Return a summary of the active Blender scene."""
        scene = bpy.context.scene
        objects = []
        for i, obj in enumerate(scene.objects):
            if i >= 50:
                break
            objects.append(
                {
                    "name": obj.name,
                    "type": obj.type,
                    "location": [
                        round(obj.location.x, 4),
                        round(obj.location.y, 4),
                        round(obj.location.z, 4),
                    ],
                    "visible": obj.visible_get(),
                }
            )

        return {
            "scene_name": scene.name,
            "object_count": len(scene.objects),
            "objects": objects,
            "materials_count": len(bpy.data.materials),
            "frame_current": scene.frame_current,
            "frame_start": scene.frame_start,
            "frame_end": scene.frame_end,
        }

    def get_object_info(self, name: str) -> dict:
        """Return detailed information about a named object."""
        obj = bpy.data.objects.get(name)
        if obj is None:
            raise ValueError(f"Object not found: {name!r}")

        info: dict = {
            "name": obj.name,
            "type": obj.type,
            "location": list(obj.location),
            "rotation_euler": list(obj.rotation_euler),
            "scale": list(obj.scale),
            "visible": obj.visible_get(),
            "materials": [
                slot.material.name for slot in obj.material_slots if slot.material
            ],
        }

        if obj.type == "MESH" and obj.data:
            mesh = obj.data
            info["mesh"] = {
                "vertices": len(mesh.vertices),
                "edges": len(mesh.edges),
                "polygons": len(mesh.polygons),
            }
            # World-space AABB
            corners = [obj.matrix_world @ mathutils.Vector(c) for c in obj.bound_box]
            min_c = [min(c[i] for c in corners) for i in range(3)]
            max_c = [max(c[i] for c in corners) for i in range(3)]
            info["world_bounding_box"] = [min_c, max_c]

        return info

    def execute_code(self, code: str) -> dict:
        """Execute arbitrary Python code in Blender's scripting context.

        ⚠️  Security note: this method grants full access to the Blender Python
        API and the host file system.  Only invoke it in a trusted, local
        environment — never expose the addon server to untrusted networks.
        """
        namespace = {"bpy": bpy}
        buf = io.StringIO()
        from contextlib import redirect_stdout

        with redirect_stdout(buf):
            exec(code, namespace)  # noqa: S102

        return {"executed": True, "output": buf.getvalue()}

    def get_viewport_screenshot(self, filepath: str, max_size: int = 800) -> dict:
        """Save a screenshot of the active 3D viewport to *filepath*."""
        if not filepath:
            raise ValueError("filepath must not be empty")

        area = next(
            (a for a in bpy.context.screen.areas if a.type == "VIEW_3D"), None
        )
        if area is None:
            raise RuntimeError("No 3D viewport found in the current screen")

        with bpy.context.temp_override(area=area):
            bpy.ops.screen.screenshot_area(filepath=filepath)

        img = bpy.data.images.load(filepath)
        try:
            w, h = img.size
            if max(w, h) > max_size:
                scale = max_size / max(w, h)
                img.scale(int(w * scale), int(h * scale))
                img.save()
                w, h = img.size
        finally:
            bpy.data.images.remove(img)

        return {"filepath": filepath, "width": w, "height": h}


# ---------------------------------------------------------------------------
# Addon global server instance
# ---------------------------------------------------------------------------

_server: BlenderMCPServer | None = None


def get_server() -> BlenderMCPServer:
    global _server
    if _server is None:
        props = bpy.context.scene.blendermcp
        _server = BlenderMCPServer(host=props.host, port=props.port)
    return _server


# ---------------------------------------------------------------------------
# Scene properties
# ---------------------------------------------------------------------------

class BlenderMCPProperties(bpy.types.PropertyGroup):
    host: StringProperty(  # type: ignore[assignment]
        name="Host",
        description="Host address to bind the MCP socket server",
        default="localhost",
    )
    port: IntProperty(  # type: ignore[assignment]
        name="Port",
        description="Port to bind the MCP socket server",
        default=9876,
        min=1024,
        max=65535,
    )
    is_running: BoolProperty(  # type: ignore[assignment]
        name="Server Running",
        default=False,
    )


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------

class BLENDERMCP_OT_StartServer(bpy.types.Operator):
    bl_idname = "blendermcp.start_server"
    bl_label = "Start MCP Server"
    bl_description = "Start the BlenderMCP socket server"

    def execute(self, context):
        global _server
        props = context.scene.blendermcp
        _server = BlenderMCPServer(host=props.host, port=props.port)
        _server.start()
        props.is_running = True
        return {"FINISHED"}


class BLENDERMCP_OT_StopServer(bpy.types.Operator):
    bl_idname = "blendermcp.stop_server"
    bl_label = "Stop MCP Server"
    bl_description = "Stop the BlenderMCP socket server"

    def execute(self, context):
        global _server
        if _server is not None:
            _server.stop()
            _server = None
        context.scene.blendermcp.is_running = False
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# UI Panel
# ---------------------------------------------------------------------------

class BLENDERMCP_PT_Panel(bpy.types.Panel):
    bl_label = "BlenderMCP"
    bl_idname = "BLENDERMCP_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BlenderMCP"

    def draw(self, context):
        layout = self.layout
        props = context.scene.blendermcp

        col = layout.column(align=True)
        col.label(text="Server Settings", icon="SETTINGS")
        col.prop(props, "host")
        col.prop(props, "port")

        layout.separator()
        if props.is_running:
            layout.operator("blendermcp.stop_server", icon="PAUSE")
            layout.label(
                text=f"Running on {props.host}:{props.port}", icon="CHECKMARK"
            )
        else:
            layout.operator("blendermcp.start_server", icon="PLAY")
            layout.label(text="Server is stopped", icon="X")


# ---------------------------------------------------------------------------
# Built-in AI Chat (no VS Code dependency)
# ---------------------------------------------------------------------------

_DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful Blender assistant running directly inside Blender. "
    "You can inspect and modify the 3D scene using the available tools.\n\n"
    "Guidelines:\n"
    "- Use get_scene_info to understand what is in the scene.\n"
    "- Use get_object_info to get details about a specific object.\n"
    "- Use execute_blender_code to create/modify objects, materials, "
    "animations, etc. via the bpy Python API.\n"
    "- Use get_viewport_screenshot to capture the current viewport.\n"
    "- Write clean, correct bpy Python code.\n"
    "- Be concise in your responses."
)

# OpenAI-compatible function-calling tool definitions (mirrors the MCP tools)
_CHAT_TOOL_DEFS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_scene_info",
            "description": (
                "Return a summary of the active Blender scene: object list "
                "(name, type, location, visibility), material count, and "
                "timeline range."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_object_info",
            "description": (
                "Return detailed information about a specific Blender object "
                "including transform, mesh statistics, materials, and world "
                "bounding box."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Exact name of the Blender object.",
                    }
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_blender_code",
            "description": (
                "Execute arbitrary Python code inside Blender using the bpy "
                "API. Use this to create objects, modify materials, set up "
                "lighting, animate, render, or perform any Blender operation. "
                "Returns any printed output."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": (
                            "Python source code to execute inside Blender."
                        ),
                    }
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_viewport_screenshot",
            "description": (
                "Capture a screenshot of the active 3D viewport and save it "
                "to a file. Returns the file path and image dimensions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": (
                            "Absolute file path where the screenshot will be "
                            "saved (e.g. /tmp/screenshot.png)."
                        ),
                    },
                    "max_size": {
                        "type": "integer",
                        "description": (
                            "Maximum pixel size for the longest edge. "
                            "Default is 800."
                        ),
                        "default": 800,
                    },
                },
                "required": ["filepath"],
            },
        },
    },
]

# ---- Chat state (module-level) -------------------------------------------

_chat_messages: list[dict] = []
_chat_busy: bool = False


# ---- Chat helpers ---------------------------------------------------------


def _execute_chat_tool(tool_name: str, arguments: dict) -> str:
    """Execute one of the MCP tools directly and return a JSON string result.

    Must be called on Blender's main thread (e.g. via ``bpy.app.timers``).
    """
    executor = BlenderMCPServer()
    try:
        if tool_name == "get_scene_info":
            result = executor.get_scene_info()
        elif tool_name == "get_object_info":
            result = executor.get_object_info(name=arguments["name"])
        elif tool_name == "execute_blender_code":
            result = executor.execute_code(code=arguments["code"])
        elif tool_name == "get_viewport_screenshot":
            result = executor.get_viewport_screenshot(
                filepath=arguments["filepath"],
                max_size=arguments.get("max_size", 800),
            )
        else:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})
        return json.dumps(result, indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


def _call_chat_api(
    api_base: str,
    api_key: str,
    model: str,
    messages: list[dict],
    tools: list[dict],
) -> dict:
    """Call an OpenAI-compatible ``/chat/completions`` endpoint."""
    url = f"{api_base.rstrip('/')}/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "tools": tools,
        "temperature": 0.7,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, context=ctx, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _schedule_redraw() -> None:
    """Tag 3-D viewports for redraw from any thread."""

    def _redraw():
        try:
            for window in bpy.context.window_manager.windows:
                for area in window.screen.areas:
                    if area.type == "VIEW_3D":
                        area.tag_redraw()
        except Exception:
            pass
        return None

    try:
        bpy.app.timers.register(_redraw, first_interval=0.0)
    except Exception:
        pass


def _chat_ui_refresh() -> float | None:
    """Timer callback that keeps the sidebar panel up-to-date while busy."""
    if not _chat_busy:
        return None  # unregister
    try:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()
    except Exception:
        pass
    return 0.5


def _chat_thread(
    api_base: str, api_key: str, model: str, system_prompt: str
) -> None:
    """Background thread: multi-turn conversation with tool calling."""
    global _chat_busy
    max_tool_rounds = 10

    try:
        # Build the API message list from the display history.
        api_messages: list[dict] = [
            {"role": "system", "content": system_prompt}
        ]
        for msg in _chat_messages:
            if msg["role"] in ("user", "assistant"):
                api_messages.append(
                    {"role": msg["role"], "content": msg["content"]}
                )

        for _ in range(max_tool_rounds):
            response = _call_chat_api(
                api_base, api_key, model, api_messages, _CHAT_TOOL_DEFS
            )

            choice = response["choices"][0]
            message = choice["message"]
            tool_calls = message.get("tool_calls")

            if not tool_calls:
                # Final text answer
                content = message.get("content", "")
                _chat_messages.append(
                    {"role": "assistant", "content": content}
                )
                break

            # The assistant wants to call tools – record its message so the
            # API sees it on the next round.
            api_messages.append(message)

            for tc in tool_calls:
                func = tc["function"]
                tool_name = func["name"]
                try:
                    tool_args = json.loads(func["arguments"])
                except (json.JSONDecodeError, TypeError):
                    tool_args = {}

                # Execute on the main thread via bpy.app.timers
                result_holder: dict = {}
                event = threading.Event()

                def _run_tool(
                    _tn=tool_name,
                    _ta=tool_args,
                    _rh=result_holder,
                    _ev=event,
                ):
                    _rh["result"] = _execute_chat_tool(_tn, _ta)
                    _ev.set()
                    return None  # unregister timer

                bpy.app.timers.register(_run_tool, first_interval=0.0)
                event.wait(timeout=30)

                tool_result = result_holder.get(
                    "result", '{"error": "Tool execution timed out"}'
                )
                api_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": tool_result,
                    }
                )

                # Show an abbreviated note in the chat panel
                _chat_messages.append(
                    {"role": "tool", "content": f"\U0001f527 {tool_name}"}
                )

    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8")
        except Exception:
            pass
        err = f"API error {exc.code}"
        if "unauthorized" in body.lower() or exc.code == 401:
            err = "Authentication failed \u2013 check your API key"
        elif exc.code == 404:
            err = "Endpoint not found \u2013 check your API Base URL"
        elif exc.code == 429:
            err = "Rate limited \u2013 please wait and try again"
        else:
            err = f"{err}: {body[:200]}"
        _chat_messages.append(
            {"role": "assistant", "content": f"\u274c {err}"}
        )
    except urllib.error.URLError as exc:
        _chat_messages.append(
            {
                "role": "assistant",
                "content": (
                    f"\u274c Connection error: {exc.reason}. "
                    "Check your API Base URL and internet connection."
                ),
            }
        )
    except Exception as exc:
        _chat_messages.append(
            {"role": "assistant", "content": f"\u274c Error: {exc}"}
        )
    finally:
        _chat_busy = False
        _schedule_redraw()


# ---- Chat properties ------------------------------------------------------


class BlenderMCPChatProperties(bpy.types.PropertyGroup):
    api_base: StringProperty(  # type: ignore[assignment]
        name="API Base URL",
        description=(
            "Base URL for an OpenAI-compatible API "
            "(e.g. https://models.inference.ai.azure.com for GitHub Models, "
            "or https://api.openai.com/v1 for OpenAI)"
        ),
        default="https://models.inference.ai.azure.com",
    )
    api_key: StringProperty(  # type: ignore[assignment]
        name="API Key",
        description="API key or token (e.g. your GitHub personal access token)",
        subtype="PASSWORD",
    )
    model: StringProperty(  # type: ignore[assignment]
        name="Model",
        description="Model identifier to use for chat completions",
        default="gpt-4o",
    )
    system_prompt: StringProperty(  # type: ignore[assignment]
        name="System Prompt",
        description="System prompt sent at the start of every conversation",
        default=_DEFAULT_SYSTEM_PROMPT,
    )
    user_message: StringProperty(  # type: ignore[assignment]
        name="Message",
        description="Type your message to the AI assistant",
    )
    show_settings: BoolProperty(  # type: ignore[assignment]
        name="Show Settings",
        default=True,
    )


# ---- Chat operators -------------------------------------------------------


class BLENDERMCP_OT_SendChat(bpy.types.Operator):
    bl_idname = "blendermcp.send_chat"
    bl_label = "Send"
    bl_description = "Send your message to the AI assistant"

    def execute(self, context):
        global _chat_busy

        props = context.scene.blendermcp_chat

        if _chat_busy:
            self.report({"WARNING"}, "Chat is busy \u2013 please wait")
            return {"CANCELLED"}

        if not props.api_key:
            self.report(
                {"ERROR"}, "Set your API key in the AI Chat settings first"
            )
            return {"CANCELLED"}

        message = props.user_message.strip()
        if not message:
            return {"CANCELLED"}

        _chat_messages.append({"role": "user", "content": message})
        props.user_message = ""
        _chat_busy = True

        t = threading.Thread(
            target=_chat_thread,
            args=(
                props.api_base,
                props.api_key,
                props.model,
                props.system_prompt,
            ),
            daemon=True,
        )
        t.start()

        # Periodic UI refresh while the AI is thinking
        bpy.app.timers.register(_chat_ui_refresh, first_interval=0.5)

        return {"FINISHED"}


class BLENDERMCP_OT_ClearChat(bpy.types.Operator):
    bl_idname = "blendermcp.clear_chat"
    bl_label = "Clear Chat"
    bl_description = "Clear the entire chat history"

    def execute(self, context):
        _chat_messages.clear()
        return {"FINISHED"}


# ---- Chat panel -----------------------------------------------------------


class BLENDERMCP_PT_ChatPanel(bpy.types.Panel):
    bl_label = "AI Chat"
    bl_idname = "BLENDERMCP_PT_chat_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BlenderMCP"

    def draw(self, context):
        layout = self.layout
        props = context.scene.blendermcp_chat

        # --- API settings (collapsible) ---
        box = layout.box()
        row = box.row()
        icon = "TRIA_DOWN" if props.show_settings else "TRIA_RIGHT"
        row.prop(
            props, "show_settings", icon=icon, text="API Settings",
            emboss=False,
        )

        if props.show_settings:
            col = box.column(align=True)
            col.prop(props, "api_base")
            col.prop(props, "api_key")
            col.prop(props, "model")

        layout.separator()

        # --- Chat history ---
        chat_box = layout.box()
        if not _chat_messages:
            chat_box.label(
                text="Send a message to start chatting!", icon="INFO",
            )
        else:
            col = chat_box.column(align=True)
            # Show the last 30 messages to keep the panel responsive
            for msg in _chat_messages[-30:]:
                role = msg["role"]
                content = msg.get("content", "")

                if role == "user":
                    col.label(text="You:", icon="USER")
                elif role == "assistant":
                    col.label(text="AI:", icon="LIGHT")
                elif role == "tool":
                    col.label(text=content, icon="TOOL_SETTINGS")
                    continue
                else:
                    continue

                for line in content.split("\n"):
                    stripped = line.strip()
                    if stripped:
                        for wrapped in textwrap.wrap(stripped, width=50):
                            col.label(text=f"  {wrapped}")
                    else:
                        col.separator()

                col.separator()

        if _chat_busy:
            layout.label(text="Thinking\u2026", icon="SORTTIME")

        layout.separator()

        # --- Input ---
        row = layout.row(align=True)
        row.prop(props, "user_message", text="")
        sub = row.row(align=True)
        sub.enabled = not _chat_busy
        sub.operator("blendermcp.send_chat", text="", icon="PLAY")

        row = layout.row()
        row.enabled = not _chat_busy
        row.operator("blendermcp.clear_chat", icon="TRASH")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_classes = (
    BlenderMCPProperties,
    BlenderMCPChatProperties,
    BLENDERMCP_OT_StartServer,
    BLENDERMCP_OT_StopServer,
    BLENDERMCP_OT_SendChat,
    BLENDERMCP_OT_ClearChat,
    BLENDERMCP_PT_Panel,
    BLENDERMCP_PT_ChatPanel,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.blendermcp = bpy.props.PointerProperty(type=BlenderMCPProperties)
    bpy.types.Scene.blendermcp_chat = bpy.props.PointerProperty(
        type=BlenderMCPChatProperties,
    )


def unregister():
    global _server
    if _server is not None:
        _server.stop()
        _server = None

    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)

    del bpy.types.Scene.blendermcp_chat
    del bpy.types.Scene.blendermcp


if __name__ == "__main__":
    register()
