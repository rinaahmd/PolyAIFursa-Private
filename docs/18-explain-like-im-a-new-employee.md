# 18 - Explain Like I am a New Employee

This chapter answers the operational questions you asked for each service.

## Frontend service

### What is it?
A Next.js web app that provides the chat interface.

### Why do we need it?
Users need a browser UI to send prompts and upload images.

### What happens if we delete it?
No user-facing interface; only backend APIs remain.

### Who talks to it?
Browser users.

### Who depends on it?
End users and demos.

### How do I debug it?
- docker compose logs -f frontend
- open browser dev tools network tab
- verify NEXT_PUBLIC_AGENT_URL points to reachable agent

### How do I know it is healthy?
- home page loads on port 3000
- chat requests are sent and receive responses

### Where is it configured?
- docker-compose.yml frontend service
- services/frontend/Dockerfile
- services/frontend/lib/api.ts

### Interview questions about it
- why build-time env var needed for browser URL wiring?
- why Next.js over plain React?

## Agent service

### What is it?
FastAPI orchestration service that manages chat and tool-calling.

### Why do we need it?
To keep frontend simple and centralize AI + tool logic.

### What happens if we delete it?
No chat orchestration, no Bedrock/yolo pipeline.

### Who talks to it?
Frontend and operators via /health.

### Who depends on it?
Frontend and yolo integration flow.

### How do I debug it?
- docker compose logs -f agent
- curl http://localhost:8000/health
- test /chat payload with curl
- inspect YOLO_SERVICE_URL and AWS variables

### How do I know it is healthy?
- /health returns 200
- /chat returns expected response schema

### Where is it configured?
- docker-compose.yml agent service
- services/agent/app.py
- services/agent/.env.example

### Interview questions about it
- how does the ReAct loop work?
- why keep image bytes away from LLM messages?
- why use tool-calling instead of direct yolo calls from frontend?

## YOLO service

### What is it?
FastAPI service that runs YOLO inference and stores prediction metadata.

### Why do we need it?
Image detection should be isolated from language orchestration.

### What happens if we delete it?
Image-analysis features stop working.

### Who talks to it?
Agent, Prometheus, and direct API testers.

### Who depends on it?
Agent image tool flow and monitoring pipeline.

### How do I debug it?
- docker compose logs -f yolo
- curl health/ready/predict endpoints
- verify S3 bucket/region credentials
- inspect prediction retrieval endpoints

### How do I know it is healthy?
- /health is 200
- /ready is 200 (when not shutting down)
- /metrics is non-empty
- /predict can process valid request

### Where is it configured?
- docker-compose.yml yolo service
- services/yolo/app.py
- services/yolo/db.py
- services/yolo/models.py
- services/yolo/s3_utils.py

### Interview questions about it
- why SQLAlchemy models here?
- why store images in S3 but metadata in DB?
- why expose both /health and /ready?

## Prometheus service

### What is it?
Metrics collector and time-series database.

### Why do we need it?
To observe API behavior over time.

### What happens if we delete it?
No centralized metrics history.

### Who talks to it?
Grafana and operators.

### Who depends on it?
Grafana dashboards.

### How do I debug it?
- docker compose logs -f prometheus
- open targets page on port 9090
- verify yolo target is UP

### How do I know it is healthy?
- /-/healthy responds.
- targets are scraping successfully.

### Where is it configured?
- docker-compose.yml prometheus service
- prometheus.yml

### Interview questions about it
- what is scrape interval tradeoff?
- why scrape yolo directly?

## Grafana service

### What is it?
Dashboard UI for metrics visualization.

### Why do we need it?
To make Prometheus data actionable for humans.

### What happens if we delete it?
Metrics remain but are hard to consume quickly.

### Who talks to it?
Operators and developers.

### Who depends on it?
Monitoring visualization workflow.

### How do I debug it?
- docker compose logs -f grafana
- verify datasource connection to prometheus

### How do I know it is healthy?
- UI loads on port 3001
- datasource test succeeds

### Where is it configured?
- docker-compose.yml grafana service

### Interview questions about it
- why separate Grafana from Prometheus?
- why host port 3001 instead of 3000?

## New employee first-week checklist
1. Read docs/01-project-overview.md.
2. Read docs/07-docker-compose.md.
3. Read docs/04-agent.md and docs/05-yolo.md.
4. Run stack and verify health endpoints.
5. Trigger one full chat with image and trace logs.
6. Open Prometheus and Grafana and confirm metrics flow.
