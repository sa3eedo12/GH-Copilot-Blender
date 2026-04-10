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
import threading
import time
import traceback

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
# Registration
# ---------------------------------------------------------------------------

_classes = (
    BlenderMCPProperties,
    BLENDERMCP_OT_StartServer,
    BLENDERMCP_OT_StopServer,
    BLENDERMCP_PT_Panel,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.blendermcp = bpy.props.PointerProperty(type=BlenderMCPProperties)


def unregister():
    global _server
    if _server is not None:
        _server.stop()
        _server = None

    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)

    del bpy.types.Scene.blendermcp


if __name__ == "__main__":
    register()
