# Image Processing MCP Server

An MCP (Model Context Protocol) server, built with [FastMCP](https://github.com/jlowin/fastmcp), that exposes image manipulation tools over HTTP. The Vision Agent connects to it as an MCP client to process images on demand.

## Prerequisites

- Python 3.10+

## Setup

Install dependencies (from `services/img-proc-mcp/`):

```bash
pip install -r requirements.txt
```

## Running

```bash
python app.py
```

The server starts at `http://localhost:9000/mcp`.

## Running Tests

```bash
pytest tests/
```

## Tools

| Tool | Description |
|---|---|
| `blur` | Apply Gaussian blur to a base64-encoded image. Takes `image_b64` and an optional `radius` (default `2.0`). Returns a base64-encoded PNG. |

## Talking to the server directly

MCP uses JSON-RPC over HTTP. You must first initialize a session and reuse the returned `mcp-session-id` header on every following request.

```bash
# 1. Initialize
curl -i -X POST http://localhost:9000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
      "protocolVersion": "2025-03-26",
      "capabilities": {},
      "clientInfo": { "name": "curl", "version": "1.0" }
    }
  }'

# 2. Confirm the handshake (use the mcp-session-id from step 1)
curl -s -X POST http://localhost:9000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "mcp-session-id: <SESSION_ID>" \
  -d '{"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}'

# 3. Call the blur tool
curl -s -X POST http://localhost:9000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "mcp-session-id: <SESSION_ID>" \
  -d '{
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/call",
    "params": {
      "name": "blur",
      "arguments": { "image_b64": "<BASE64_ENCODED_IMAGE>", "radius": 2.0 }
    }
  }'
```

In practice, the Vision Agent talks to this server through `langchain-mcp-adapters` (`MultiServerMCPClient`) instead of raw `curl` — see `services/agent/README.md`.
