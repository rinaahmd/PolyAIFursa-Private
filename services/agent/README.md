# Vision Agent

A LangChain-powered AI vision agent with a manual ReAct loop. Accepts text and base64-encoded images, and can call tools (e.g. YOLO object detection) to answer questions.

## Prerequisites

- Python 3.10+
- A running YOLO service (optional - only needed for `detect_objects`)
- A running [img-proc-mcp](../img-proc-mcp/README.md) server (optional - only needed for `blur_image`)


## Setup

Install dependencies (from `services/agent/`):

```bash
pip install -r requirements.txt
```

Configure environment:

```bash
cp .env.example .env
# Edit .env and set MODEL, AWS_REGION, AWS_S3_BUCKET, and YOLO_SERVICE_URL
```

`.env` variables:

| Variable | Default | Description |
|---|---|---|
| `MODEL` | `amazon.nova-micro-v1:0` | Bedrock model passed to `init_chat_model` |
| `AWS_REGION` | `us-east-1` | AWS region for Bedrock and S3 |
| `AWS_S3_BUCKET` | - | S3 bucket for original and predicted images |
| `YOLO_SERVICE_URL` | `http://host.docker.internal:8080` (docker run) or `http://yolo:8080` (Docker Compose) | URL of the YOLO microservice |
| `IMG_PROC_MCP_URL` | `http://host.docker.internal:9000/mcp` (docker run) or `http://img-proc-mcp:9000/mcp` (Docker Compose) | URL of the image-processing MCP server |

Deployment notes:

- Docker run on EC2/Linux host: use `YOLO_SERVICE_URL=http://host.docker.internal:8080` and run the agent container with `--add-host=host.docker.internal:host-gateway`.
- Docker Compose: use `YOLO_SERVICE_URL=http://yolo:8080` so the agent reaches the `yolo` service by container name.

## Running

```bash
cd services/agent
python app.py
```

The server starts at `http://localhost:8000`.

## Testing with curl

### Health check

```bash
curl http://localhost:8000/health
```

Expected response:
```json
{"status": "ok"}
```

### Plain text message

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "Hello! What can you do?"}], "chat_id": "demo-chat"}'
```

### Send a message with an image

```bash
echo "{\"messages\":[{\"role\":\"user\",\"content\":\"What objects are in this image?\",\"image_base64\":\"$(base64 -w0 beatles.jpeg)\"}],\"chat_id\":\"demo-chat\"}" \
  | curl -X POST http://localhost:8000/chat \
         -H "Content-Type: application/json" \
         -d @-
```

## API Reference

### `POST /chat`

Request body:

```json
{
  "messages": [
    {
      "role": "user | assistant",
      "content": "string",
      "image_base64": "optional base64-encoded image for user messages"
    }
  ],
  "chat_id": "optional string"
}
```

Response:

```json
{
  "response": "string",
  "prediction_id": "string | null",
  "annotated_image": "string | null",
  "processed_image": "string | null",
  "agent_loop_time_s": 0.0,
  "iterations": 1,
  "tools_called": [],
  "context_limit_exceeded": false,
  "tokens_used": {
    "input": null,
    "output": null,
    "total": null
  }
}
```

`processed_image` is a base64-encoded PNG set when the agent calls the `blur_image` tool (see below).

### `GET /health`

Returns `{"status": "ok"}` when the service is running.
