# 16 - Interview Preparation

## 60-second system pitch
This project is a multi-service AI assistant platform. A Next.js frontend sends chat requests to a FastAPI agent that uses Bedrock for language reasoning and calls a YOLO FastAPI service for image detection when needed. Images are stored in S3, predictions are persisted through SQLAlchemy, and the stack is deployed with GitHub Actions over SSH to EC2 using Docker Compose. Prometheus and Grafana provide observability.

## How to explain request flow
1. Browser opens frontend.
2. Frontend sends POST /chat to agent.
3. Agent invokes Bedrock model.
4. If tool-calling is needed, agent uploads image to S3 and calls yolo /predict.
5. yolo runs inference, stores results, returns prediction data.
6. Agent fetches annotated image and returns final response to frontend.

## How to explain deployment flow
1. Push to dev/main triggers deploy workflow.
2. GitHub runner SSHes into EC2.
3. Server repo is synced exactly with branch via fetch/checkout/reset.
4. compose pull/down/build/up refreshes containers.
5. curl health checks gate success.

## How to explain Docker design choices
- containers isolate services.
- compose defines consistent topology.
- service-name DNS removes IP hardcoding.
- build args are used where browser-facing configuration must be embedded.

## How to explain AWS usage
- Bedrock: model inference for language responses.
- S3: shared image storage between agent and yolo.
- EC2: deployment runtime host.
- ~/.aws mount allows boto3 auth without hardcoding keys in images.

## Common interview questions and answers

### Why split agent and yolo?
Separation of concerns: language orchestration and image inference have different scaling and lifecycle patterns.

### Why not call yolo directly from frontend?
Backend orchestration centralizes validation, cloud credentials, and security boundaries.

### Why reset --hard in deployment?
To ensure server state exactly matches the branch commit being deployed.

### Why Prometheus and Grafana?
Prometheus collects metrics; Grafana visualizes trends and health for faster debugging and operations.

### What is the highest-risk misconfiguration?
Incorrect build/runtime URL wiring (NEXT_PUBLIC_AGENT_URL and YOLO_SERVICE_URL) and missing AWS credentials.
