import base64
import io
import json
import logging
import os
import time
from contextvars import ContextVar
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logging.getLogger("langchain").setLevel(logging.DEBUG)
logging.getLogger("langchain_core").setLevel(logging.DEBUG)

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from pydantic import BaseModel

YOLO_SERVICE_URL = os.environ.get("YOLO_SERVICE_URL", "http://localhost:8080")
MODEL = os.environ.get("MODEL")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

# Text-only models


model = init_chat_model(
    "amazon.nova-micro-v1:0",
    model_provider="bedrock",
    region_name="us-east-1",
)

SYSTEM_PROMPT = (
    "You are an AI vision assistant. You help users understand and analyze images. "
    "Use the available tools to extract information from images. "
)

_current_image_b64: ContextVar[Optional[str]] = ContextVar("current_image_b64", default=None)

@tool
def detect_objects() -> str:
    """Detect and identify objects in the image provided by the user using YOLO object detection."""
    image_b64 = _current_image_b64.get()
    if not image_b64:
        return json.dumps({"error": "No image was provided by the user."})

    image_bytes = base64.b64decode(image_b64)
    with httpx.Client(timeout=30.0) as client:
        response = client.post(
            f"{YOLO_SERVICE_URL}/predict",
            files={"file": ("image.jpg", io.BytesIO(image_bytes), "image/jpeg")},
        )
        response.raise_for_status()
    return json.dumps(response.json())


# Registry: map tool name -> tool function
TOOLS = {
    detect_objects.name: detect_objects
}

llm = init_chat_model(
    MODEL,
    model_provider="bedrock",
    region_name=AWS_REGION,
    temperature=0,
)
llm_with_tools = llm.bind_tools(list(TOOLS.values()))


def _stringify_content(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                text_parts.append(item["text"])
            else:
                text_parts.append(str(item))
        return "\n".join(text_parts)
    if content is None:
        return ""
    return str(content)


def _parse_tool_json(content) -> Optional[dict]:
    text = _stringify_content(content)
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


def _fetch_annotated_image(prediction_id: str) -> Optional[str]:
    image_url = f"{YOLO_SERVICE_URL}/prediction/{prediction_id}/image"
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(image_url)
            response.raise_for_status()
        return base64.b64encode(response.content).decode("utf-8")
    except (httpx.HTTPError, ValueError, TypeError):
        return None




def run_agent(history: list, max_iterations: int = 10) -> dict:
    """
    Simple ReAct loop:
      1. Send messages to the LLM.
      2. If the LLM requests tool calls, execute them and append results.
      3. Repeat until the LLM returns a plain text response.
      4. Stop after max_iterations to avoid infinite loops.
    """
    messages = [SystemMessage(content=SYSTEM_PROMPT)] + history
    start_time = time.perf_counter()
    iterations = 0
    tools_called: list[str] = []
    prediction_id: Optional[str] = None
    annotated_image: Optional[str] = None
    context_limit_exceeded = False
    final_response = ""

    for _ in range(max_iterations):
        iterations += 1
        response: AIMessage = llm_with_tools.invoke(messages)
        print("TOOL CALLS:", response.tool_calls)
        print("CONTENT:", response.content)
        messages.append(response)

        # No tool calls, the model produced its final answer
        if not response.tool_calls:
            final_response = _stringify_content(response.content)
            break

        # Execute every tool the model requested
        for tool_call in response.tool_calls:
            tool_name = tool_call.get("name", "")
            if tool_name:
                tools_called.append(tool_name)

            tool_fn = TOOLS.get(tool_name)
            if tool_fn is None:
                tool_result = ToolMessage(
                    tool_call_id=tool_call.get("id", "unknown"),
                    content=json.dumps({"error": f"Unknown tool: {tool_name}"}),
                )
                messages.append(tool_result)
                continue

            try:
                tool_result = tool_fn.invoke(tool_call)
            except Exception as tool_exc:  # noqa: BLE001 - tool failures must not crash chat
                tool_result = ToolMessage(
                    tool_call_id=tool_call.get("id", "unknown"),
                    content=json.dumps({"error": f"Tool execution failed: {tool_exc}"}),
                )

            messages.append(tool_result)

            if tool_name == detect_objects.name:
                parsed = _parse_tool_json(tool_result.content)
                if parsed:
                    parsed_prediction_id = parsed.get("prediction_uid")
                    if isinstance(parsed_prediction_id, str) and parsed_prediction_id:
                        prediction_id = parsed_prediction_id
                        annotated_image = _fetch_annotated_image(parsed_prediction_id)
    else:
        context_limit_exceeded = True
        final_response = "Agent stopped because it reached the maximum number of tool iterations."

    return {
        "response": final_response,
        "prediction_id": prediction_id,
        "annotated_image": annotated_image,
        "agent_loop_time_s": time.perf_counter() - start_time,
        "iterations": iterations,
        "tools_called": tools_called,
        "context_limit_exceeded": context_limit_exceeded,
    }



app = FastAPI(title="Vision Agent")

app.add_middleware(
    CORSMiddleware,
   allow_origins=[
        "http://localhost:3000",
        "http://34.224.235.157:3000","http://3.214.66.146:3000",
        "http://rina-dev.fursa.click:3000",
    ],    allow_methods=["POST", "GET"],
    allow_headers=["Content-Type"],
)


class ChatMessage(BaseModel):
    role: str                           # "user" or "assistant"
    content: str
    image_base64: Optional[str] = None  # only on user messages that carry an image


class ChatRequest(BaseModel):
    messages: list[ChatMessage]         # full conversation thread, oldest first


class ChatResponse(BaseModel):
    response: str
    prediction_id: str | None = None
    annotated_image: str | None = None
    agent_loop_time_s: float
    iterations: int
    tools_called: list[str]
    context_limit_exceeded: bool


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    lc_messages = []
    latest_image = None

    for msg in request.messages:
        if msg.role == "user":
            if msg.image_base64:
                latest_image = msg.image_base64          # saved for detect_objects tool
                content = msg.content + "\n[An image was uploaded. Use existing tools to analyze it according to user instructions.]"
            else:
                content = msg.content
            lc_messages.append(HumanMessage(content=content))
        else:
            lc_messages.append(AIMessage(content=msg.content))

    token = _current_image_b64.set(latest_image)
    try:
        return ChatResponse(**run_agent(lc_messages))
    finally:
        _current_image_b64.reset(token)


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
