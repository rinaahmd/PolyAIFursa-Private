# Image Processing MCP Server

An MCP (Model Context Protocol) server, built with [FastMCP](https://github.com/jlowin/fastmcp), that exposes image manipulation tools over HTTP. The Vision Agent connects to it as an MCP client to process images on demand.

This server is stateless with respect to the *agent's* conversation state (chat history, edit chains, etc. all live in the agent), but it does talk to S3 directly: every tool takes an S3 key pointing at its input image(s) AND an `output_s3_key` the caller chooses, downloads the input(s) from S3, performs the operation, uploads the result to `output_s3_key` (overwriting whatever was already there, safe even if it's the same key as an input), and returns that key back. No image bytes are ever sent in the MCP request or response, and this server never invents its own S3 keys - the caller (the agent) is in full control of where results land, which lets it reuse a small, fixed set of scratch keys per chat instead of accumulating a new S3 object on every tool call.

## Prerequisites

- Python 3.10+
- AWS credentials available to the process (an EC2 instance role, or a mounted `~/.aws` locally - see `docker-compose.yml`). Never hard-code credentials in this service.
- `AWS_REGION` and `AWS_S3_BUCKET` environment variables set.

## Setup

Install dependencies (from `services/img-proc-mcp/`):

```bash
pip install -r requirements.txt
```

```bash
export AWS_REGION=us-east-1
export AWS_S3_BUCKET=your-bucket-name
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

Every tool downloads its input(s) from S3, applies the operation, uploads the PNG result to the caller-supplied `output_s3_key` (overwritten in place if it already exists), and returns that same key as a plain string.

| Tool | Description |
|---|---|
| `blur` | Apply Gaussian blur. Takes `input_s3_key`, `output_s3_key`, and an optional `radius` (default `2.0`). |
| `rotate` | Rotate counter-clockwise by `angle` degrees. Takes `input_s3_key`, `output_s3_key`, `angle`, optional `expand` (default `True`). |
| `flip` | Flip an image. Takes `input_s3_key`, `output_s3_key`, `direction` (`"horizontal"` default, or `"vertical"`). |
| `resize` | Resize to an exact `width` x `height` in pixels. Takes `input_s3_key`, `output_s3_key`, `width`, `height`. |
| `crop` | Crop to the box `(left, top, right, bottom)` in pixels. Takes `input_s3_key`, `output_s3_key`, `left`, `top`, `right`, `bottom`. |
| `add_noise` | Add salt-and-pepper noise. Takes `input_s3_key`, `output_s3_key`, optional `amount` (default `0.05`, fraction of pixels affected). |
| `paste` | Paste `region_s3_key` into `base_s3_key` at `(left, top)`, overwriting that area, writing the composited result to `output_s3_key`. Used to composite a transformed sub-region back into the full image after an object-scoped edit - `output_s3_key` is typically the same as `base_s3_key`, so the full image is updated in place. |

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

# 3. Call the blur tool - input_s3_key must already exist in AWS_S3_BUCKET.
# output_s3_key can be any key you choose, including the same as input_s3_key
# to blur the image in place.
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
      "arguments": {
        "input_s3_key": "some-chat-id/scratch/base.png",
        "output_s3_key": "some-chat-id/scratch/base.png",
        "radius": 2.0
      }
    }
  }'
```

In practice, the Vision Agent talks to this server through `langchain-mcp-adapters` (`MultiServerMCPClient`) instead of raw `curl` — see `services/agent/README.md`.
