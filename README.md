# GH-Copilot-Blender

Connect **GitHub Copilot** (or any MCP-capable AI client) to **Blender** through the
[Model Context Protocol (MCP)](https://modelcontextprotocol.io/).

You can interact with the AI in **two ways**:

1. **Built-in AI Chat** — talk to an AI assistant directly inside Blender's
   sidebar. No VS Code required.
2. **VS Code + Copilot Agent Mode** — use GitHub Copilot in VS Code with the
   MCP server bridge (the original workflow).

---

## How it works

### Option A — Built-in AI Chat (recommended, no VS Code needed)

```
Blender addon  ──(HTTPS / OpenAI-compatible API)──►  AI provider
  (addon.py)                                         (GitHub Models, OpenAI, etc.)
```

The addon includes an **AI Chat** panel in the 3D Viewport sidebar.
You configure an API key and endpoint, then chat with the AI directly inside
Blender.  The AI can call the same tools (scene info, object info, execute code,
screenshot) without any external processes.

### Option B — VS Code + MCP

```
GitHub Copilot  ──(MCP / stdio)──►  blender-mcp server  ──(TCP / JSON)──►  Blender addon
     (VS Code)                        (Python process)                         (addon.py)
```

1. The **Blender addon** (`addon.py`) runs a small TCP socket server inside Blender
   that accepts JSON commands and executes them on Blender's main thread.
2. The **MCP server** (`src/blender_mcp/server.py`) implements the Model Context
   Protocol over stdio and forwards Copilot's tool calls to the Blender addon.
3. **VS Code** (with GitHub Copilot) is configured via `.vscode/mcp.json` to launch the
   MCP server automatically.

---

## Prerequisites

### Built-in AI Chat (Option A)

| Tool | Version |
|------|---------|
| Blender | 3.0 or newer |
| An OpenAI-compatible API key | — |

Supported providers: **GitHub Models**, **OpenAI**, **Azure OpenAI**, or any
service with an OpenAI-compatible `/chat/completions` endpoint.

### VS Code + MCP (Option B)

| Tool | Version |
|------|---------|
| Blender | 3.0 or newer |
| Python | 3.10 or newer |
| [uv](https://astral.sh/uv/) | latest |
| VS Code | latest |
| GitHub Copilot extension | latest |

### Install uv

**macOS / Linux**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Windows (PowerShell)**
```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

---

## Installation

### 1 – Install the Blender addon

1. Download `addon.py` from this repository.
2. Open Blender → **Edit → Preferences → Add-ons → Install…**
3. Select `addon.py` and click **Install Add-on**.
4. Enable the addon by ticking the checkbox next to **Interface: Blender MCP**.

### 2 – Start the addon server in Blender

1. In the 3D Viewport press **N** to open the sidebar.
2. Select the **BlenderMCP** tab.
3. Optionally change the **Host** / **Port** (default `localhost:9876`).
4. Click **Start MCP Server**.

You should see *"Running on localhost:9876"* in the panel.

> **Note:** The MCP socket server is only needed for *Option B* (VS Code).
> The built-in AI Chat works without starting the socket server.

### 3 – Built-in AI Chat (Option A)

1. In the 3D Viewport sidebar (**N**), select the **BlenderMCP** tab.
2. Expand the **AI Chat** panel.
3. Open **API Settings** and fill in:
   - **API Base URL** — for GitHub Models use
     `https://models.inference.ai.azure.com`, for OpenAI use
     `https://api.openai.com/v1`.
   - **API Key** — your GitHub personal access token (for GitHub Models) or
     OpenAI API key.
   - **Model** — e.g. `gpt-4o`.
4. Type a message in the text field and click ▶ (Send).
5. The AI assistant can inspect your scene, create objects, run code, and take
   screenshots — all directly inside Blender.

### 4 – Configure VS Code (Option B)

The repository already ships with `.vscode/mcp.json`, so VS Code will automatically
offer to start the `blender` MCP server when you open the project folder.

If you need to configure it manually, add the following to your
**User Settings** (`mcp.json`) or workspace `.vscode/mcp.json`:

```json
{
  "servers": {
    "blender": {
      "type": "stdio",
      "command": "uvx",
      "args": ["blender-mcp"],
      "env": {
        "BLENDER_HOST": "localhost",
        "BLENDER_PORT": "9876"
      }
    }
  }
}
```

### 5 – Use GitHub Copilot Agent Mode (Option B)

1. Open the **Copilot Chat** panel in VS Code (`Ctrl+Alt+I` / `⌃⌥I`).
2. Switch to **Agent mode** (click the mode selector and choose **Agent**).
3. You should see a 🔨 (tools) icon — the Blender MCP tools are now available.
4. Start prompting!

---

## Available MCP tools

| Tool | Description |
|------|-------------|
| `get_scene_info` | Returns a summary of the active Blender scene (objects, materials, timeline). |
| `get_object_info` | Returns detailed info (transform, mesh stats, materials) for a named object. |
| `execute_blender_code` | Executes arbitrary Python code inside Blender. |
| `get_viewport_screenshot` | Saves a screenshot of the active 3D viewport to a file. |

---

## Example prompts

> "What objects are in the current Blender scene?"

> "Create a red metallic sphere at position (2, 0, 1) with a radius of 0.5."

> "Add a sun lamp pointing downward and set its energy to 5."

> "Give me the vertex count of the object named 'Suzanne'."

> "Save a screenshot of the viewport to /tmp/preview.png."

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `BLENDER_HOST` | `localhost` | Host where the Blender addon server is listening. |
| `BLENDER_PORT` | `9876` | Port where the Blender addon server is listening. |

---

## Running the MCP server manually (for debugging)

```bash
# From the repo root
uv run blender-mcp
```

Or install the package locally first:

```bash
uv pip install -e .
blender-mcp
```

---

## Security note

The `execute_blender_code` tool runs arbitrary Python code inside Blender
and has full access to your file system.  Only use this integration in a
trusted, local environment.

---

## License

MIT
