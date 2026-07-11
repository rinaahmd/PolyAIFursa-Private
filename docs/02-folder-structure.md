# 02 - Folder Structure

```text
PolyAIFursa-Private/
|-- .agents/skills/data-layer/
|-- .github/workflows/
|-- docs/
|-- services/frontend/
|-- services/agent/
|-- services/yolo/
|-- docker-compose.yml
|-- prometheus.yml
|-- README.md
`-- AGENTS.md
```

## Responsibilities
- .github/workflows: CI and CD automation.
- services/frontend: chat UI and browser API client.
- services/agent: orchestration backend and Bedrock tool-calling loop.
- services/yolo: object detection backend, metrics, persistence.
- docs: split technical documentation.
- docker-compose.yml: runtime topology.
- prometheus.yml: scrape configuration.
